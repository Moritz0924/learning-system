from __future__ import annotations

import re
from dataclasses import replace
from typing import Sequence

from backend.app.services.document_parsing.models import DocumentBlockType

from .config import SizeGuardPolicy
from .domain import ChunkCandidate, SemanticBoundary, SemanticSegment, SemanticUnit
from .errors import HybridChunkingInvariantViolation
from .ports import TokenCounterPort
from .renderer import ChunkRenderer


class SizeGuard:
    def __init__(
        self,
        *,
        token_counter: TokenCounterPort,
        policy: SizeGuardPolicy,
        renderer: ChunkRenderer | None = None,
    ) -> None:
        self.token_counter = token_counter
        self.policy = policy
        self.renderer = renderer or ChunkRenderer()

    def apply(self, segments: Sequence[SemanticSegment]) -> list[ChunkCandidate]:
        entries: list[tuple[ChunkCandidate, SemanticBoundary | None, SemanticBoundary | None]] = []
        for segment in segments:
            segment_candidates = self._apply_segment(segment)
            for index, candidate in enumerate(segment_candidates):
                entries.append((
                    replace(candidate, boundaries=segment.boundaries),
                    segment.boundary_before if index == 0 else None,
                    segment.boundary_after if index == len(segment_candidates) - 1 else None,
                ))
        index = 0
        while index < len(entries):
            candidate, before, after = entries[index]
            if self.token_counter.count(candidate.content) >= self.policy.min_tokens:
                index += 1
                continue
            left = entries[index - 1] if index else None
            right = entries[index + 1] if index + 1 < len(entries) else None
            left_merge = self._merge_candidates(left[0], candidate) if left is not None else None
            right_merge = self._merge_candidates(candidate, right[0]) if right is not None else None
            if left_merge is not None and right_merge is not None:
                choose_left = _boundary_score(before) <= _boundary_score(after)
            else:
                choose_left = left_merge is not None
            if choose_left and left_merge is not None:
                entries[index - 1] = (left_merge, left[1], after)
                del entries[index]
                index = max(index - 1, 0)
            elif right_merge is not None:
                entries[index + 1] = (right_merge, before, right[2])
                del entries[index]
            else:
                index += 1
        return [self._with_count(candidate) for candidate, _, _ in entries]

    def _merge_candidates(self, left: ChunkCandidate, right: ChunkCandidate) -> ChunkCandidate | None:
        if left.chunk_type != right.chunk_type or left.chunk_type in {"code", "table"}:
            return None
        combined = f"{left.content}\n\n{right.content}".strip()
        if self.token_counter.count(combined) > self.policy.max_tokens:
            return None
        metadata = {"size_guard": {"action": "tiny_merge"}}
        return replace(
            left,
            content=combined,
            source_unit_ids=left.source_unit_ids + right.source_unit_ids,
            page_end=max(left.page_end, right.page_end),
            metadata=metadata,
            boundaries=left.boundaries + right.boundaries,
        )

    def _apply_segment(self, segment: SemanticSegment) -> list[ChunkCandidate]:
        units = list(segment.units)
        if not units:
            return []
        if units[0].block_type is DocumentBlockType.TABLE:
            return self._split_table(units)
        if units[0].block_type is DocumentBlockType.CODE:
            return self._split_code(units)
        return self._split_text(units)

    def _split_text(self, units: list[SemanticUnit]) -> list[ChunkCandidate]:
        result: list[ChunkCandidate] = []
        current: list[SemanticUnit] = []
        for unit in units:
            trial = current + [unit]
            if current and self._count(trial) > self.policy.max_tokens:
                result.append(self._candidate(current, "oversize_split", boundaries=()))
                current = [unit]
            else:
                current = trial
        if current:
            result.append(self._candidate(current, "kept", boundaries=()))
        return self._force_safe(result)

    def _split_code(self, units: list[SemanticUnit]) -> list[ChunkCandidate]:
        text = "\n".join(unit.text for unit in units)
        opening, body, language = _fence_parts(text)
        lines = body.splitlines() if opening else text.splitlines()
        chunks: list[ChunkCandidate] = []
        current: list[str] = []
        source = units[0]
        for group in _code_groups(lines):
            trial = current + group
            rendered = _fenced(trial, language) if opening else "\n".join(trial)
            if current and self.token_counter.count(rendered) > self.policy.max_tokens:
                chunks.append(self._candidate([replace(source, text=_fenced(current, language) if opening else "\n".join(current))], "code_line_split", boundaries=()))
                current = []
            rendered_group = _fenced(group, language) if opening else "\n".join(group)
            if not current and self.token_counter.count(rendered_group) > self.policy.max_tokens:
                chunks.extend(self._code_group_fragments(source, group, language=language, fenced=opening))
            else:
                current.extend(group)
        if current:
            chunks.append(self._candidate([replace(source, text=_fenced(current, language) if opening else "\n".join(current))], "code_line_split", boundaries=()))
        return self._force_safe(chunks)

    def _code_group_fragments(
        self,
        source: SemanticUnit,
        group: list[str],
        *,
        language: str,
        fenced: bool,
    ) -> list[ChunkCandidate]:
        fragments: list[ChunkCandidate] = []
        current: list[str] = []
        for block in _blank_line_groups(group):
            rendered = _fenced(current + block, language) if fenced else "\n".join(current + block)
            if current and self.token_counter.count(rendered) > self.policy.max_tokens:
                fragments.append(self._candidate(
                    [replace(source, text=_fenced(current, language) if fenced else "\n".join(current))],
                    "code_blank_line_split",
                    boundaries=(),
                ))
                current = []
            rendered_block = _fenced(block, language) if fenced else "\n".join(block)
            if not current and self.token_counter.count(rendered_block) > self.policy.max_tokens:
                fragments.extend(self._code_line_groups(source, block, language=language, fenced=fenced))
            else:
                current.extend(block)
        if current:
            fragments.append(self._candidate(
                [replace(source, text=_fenced(current, language) if fenced else "\n".join(current))],
                "code_blank_line_split",
                boundaries=(),
            ))
        return fragments

    def _code_line_groups(
        self,
        source: SemanticUnit,
        lines: list[str],
        *,
        language: str,
        fenced: bool,
    ) -> list[ChunkCandidate]:
        fragments: list[ChunkCandidate] = []
        current: list[str] = []
        for line in lines:
            rendered = _fenced(current + [line], language) if fenced else "\n".join(current + [line])
            if current and self.token_counter.count(rendered) > self.policy.max_tokens:
                fragments.append(self._candidate(
                    [replace(source, text=_fenced(current, language) if fenced else "\n".join(current))],
                    "code_line_split",
                    boundaries=(),
                ))
                current = []
            rendered_line = _fenced([line], language) if fenced else line
            if not current and self.token_counter.count(rendered_line) > self.policy.max_tokens:
                fragments.extend(self._code_line_fragments(source, line, language=language, fenced=fenced))
            else:
                current.append(line)
        if current:
            fragments.append(self._candidate(
                [replace(source, text=_fenced(current, language) if fenced else "\n".join(current))],
                "code_line_split",
                boundaries=(),
            ))
        return fragments

    def _split_table(self, units: list[SemanticUnit]) -> list[ChunkCandidate]:
        source = units[0]
        lines = "\n".join(unit.text for unit in units).splitlines()
        table_contents = self._table_contents(lines)
        return [
            self._with_count(self._candidate([replace(source, text=content)], "table_row_split", boundaries=()))
            for content in table_contents
        ]

    def _table_contents(self, lines: list[str]) -> list[str]:
        if len(lines) < 3:
            raise HybridChunkingInvariantViolation("table fallback requires a header and at least one row")
        header = lines[:2]
        if not _markdown_cells(header[0]) or not _markdown_cells(header[1]):
            raise HybridChunkingInvariantViolation("table fallback requires valid markdown table headers")
        chunks: list[ChunkCandidate] = []
        rows: list[str] = []
        for row in lines[2:]:
            row_fragments = self._table_row_fragments(header, row)
            for fragment in row_fragments:
                trial = rows + [fragment]
                rendered = "\n".join([*header, *trial])
                if rows and self.token_counter.count(rendered) > self.policy.max_tokens:
                    chunks.append("\n".join([*header, *rows]))
                    rows = [fragment]
                else:
                    rows = trial
                if self.token_counter.count("\n".join([*header, *rows])) > self.policy.max_tokens:
                    raise HybridChunkingInvariantViolation("unable to produce token-safe table fragment")
        if rows:
            chunks.append("\n".join([*header, *rows]))
        return chunks

    def _table_row_fragments(self, header: list[str], row: str) -> list[str]:
        rendered = "\n".join([*header, row])
        if self.token_counter.count(rendered) <= self.policy.max_tokens:
            return [row]
        cells = _markdown_cells(row)
        header_cells = _markdown_cells(header[0])
        if cells is None or header_cells is None or len(cells) != len(header_cells):
            raise HybridChunkingInvariantViolation("unable to split invalid oversized table row")
        fragments: list[str] = []
        for cell_index, cell in enumerate(cells):
            fragments.extend(self._cell_fragments(header, len(cells), cell_index, cell))
        return fragments

    def _cell_fragments(
        self,
        header: list[str],
        column_count: int,
        cell_index: int,
        cell: str,
    ) -> list[str]:
        if not cell:
            return [_markdown_row(["" for _ in range(column_count)])]
        fragments: list[str] = []
        start = 0
        while start < len(cell):
            end = len(cell)
            while end > start:
                cells = ["" for _ in range(column_count)]
                cells[cell_index] = cell[start:end]
                row = _markdown_row(cells)
                if self.token_counter.count("\n".join([*header, row])) <= self.policy.max_tokens:
                    fragments.append(row)
                    start = end
                    break
                end -= 1
            else:
                raise HybridChunkingInvariantViolation("table header leaves no room for a token-safe cell fragment")
        return fragments

    def _code_line_fragments(
        self,
        source: SemanticUnit,
        line: str,
        *,
        language: str,
        fenced: bool,
    ) -> list[ChunkCandidate]:
        fragments: list[ChunkCandidate] = []
        start = 0
        while start < len(line):
            end = len(line)
            while end > start:
                content = _fenced([line[start:end]], language) if fenced else line[start:end]
                if self.token_counter.count(content) <= self.policy.max_tokens:
                    fragments.append(self._candidate(
                        [replace(source, text=content)],
                        "code_line_fragment",
                        boundaries=(),
                    ))
                    start = end
                    break
                end -= 1
            else:
                raise HybridChunkingInvariantViolation("code fence leaves no room for a token-safe line fragment")
        return fragments

    def _force_safe(self, candidates: list[ChunkCandidate]) -> list[ChunkCandidate]:
        result: list[ChunkCandidate] = []
        for candidate in candidates:
            if self.token_counter.count(candidate.content) <= self.policy.max_tokens:
                result.append(self._with_count(candidate))
                continue
            if candidate.chunk_type == "table":
                for content in self._table_contents(candidate.content.splitlines()):
                    result.append(self._with_count(replace(
                        candidate,
                        content=content,
                        metadata={**candidate.metadata, "size_guard": {"action": "table_structure_fallback"}},
                    )))
                continue
            if candidate.chunk_type == "code":
                opening, body, language = _fence_parts(candidate.content)
                source = SemanticUnit(
                    unit_id="size-guard-code-fallback",
                    text=candidate.content,
                    source_unit_ids=candidate.source_unit_ids,
                    page_start=candidate.page_start,
                    page_end=candidate.page_end,
                    heading_path=candidate.heading_path,
                    block_type=DocumentBlockType.CODE,
                    order=0,
                )
                for line in (body.splitlines() if opening else candidate.content.splitlines()):
                    for fragment in self._code_line_fragments(source, line, language=language, fenced=opening):
                        result.append(self._with_count(replace(
                            fragment,
                            metadata={**fragment.metadata, "size_guard": {"action": "code_structure_fallback"}},
                        )))
                continue
            text = candidate.content
            start = 0
            while start < len(text):
                end = min(len(text), start + max(1, self.policy.max_tokens))
                while end > start and self.token_counter.count(text[start:end]) > self.policy.max_tokens:
                    end -= 1
                if end <= start:
                    raise HybridChunkingInvariantViolation("unable to produce token-safe chunk")
                piece = text[start:end]
                result.append(self._with_count(replace(
                    candidate,
                    content=piece,
                    metadata={**candidate.metadata, "size_guard": {"action": "hard_fallback"}},
                )))
                start = end
        return result

    def _candidate(self, units: list[SemanticUnit], action: str, *, boundaries: tuple = ()) -> ChunkCandidate:
        content = self.renderer.render(tuple(units))
        return ChunkCandidate(
            content=content,
            chunk_type=units[0].block_type.value,
            heading_path=units[0].heading_path,
            source_unit_ids=tuple(source_id for unit in units for source_id in unit.source_unit_ids),
            page_start=min(unit.page_start for unit in units),
            page_end=max(unit.page_end for unit in units),
            metadata={"size_guard": {"action": action}},
            boundaries=tuple(boundaries),
        )

    def _with_count(self, candidate: ChunkCandidate) -> ChunkCandidate:
        token_count = self.token_counter.count(candidate.content)
        if token_count > self.policy.max_tokens:
            raise HybridChunkingInvariantViolation("final rendered chunk exceeds max_tokens")
        metadata = dict(candidate.metadata)
        size = dict(metadata.get("size_guard", {}))
        size.update({
            "min_tokens": self.policy.min_tokens,
            "target_tokens": self.policy.target_tokens,
            "max_tokens": self.policy.max_tokens,
            "token_count": token_count,
        })
        metadata["size_guard"] = size
        return replace(candidate, metadata=metadata)

    def _count(self, units: list[SemanticUnit]) -> int:
        return self.token_counter.count(self.renderer.render(tuple(units)))


def _fence_parts(text: str) -> tuple[bool, str, str]:
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].lstrip().startswith("```") and lines[-1].strip() == "```":
        return True, "\n".join(lines[1:-1]), lines[0].strip()[3:].strip()
    return False, text, ""


def _fenced(lines: list[str], language: str) -> str:
    return "\n".join([f"```{language}".rstrip(), *lines, "```"])


def _boundary_score(boundary: SemanticBoundary | None) -> float:
    return boundary.boundary_score if boundary is not None else float("inf")


def _markdown_cells(row: str) -> list[str] | None:
    stripped = row.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _markdown_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _code_groups(lines: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if current and re.match(r"\s*(?:async\s+def|def|class)\s+", line):
            groups.append(current)
            current = []
        current.append(line)
    if current:
        groups.append(current)
    return groups


def _blank_line_groups(lines: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        current.append(line)
        if not line.strip():
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups
