from __future__ import annotations

import re
from dataclasses import replace
from typing import Sequence

from backend.app.services.document_parsing.models import DocumentBlockType

from .config import SizeGuardPolicy
from .domain import ChunkCandidate, SemanticSegment, SemanticUnit
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
        candidates: list[ChunkCandidate] = []
        pending: ChunkCandidate | None = None
        for segment in segments:
            segment_candidates = [
                replace(candidate, boundaries=segment.boundaries)
                for candidate in self._apply_segment(segment)
            ]
            if pending is not None and segment_candidates:
                merged = self._merge_if_tiny(pending, segment_candidates[0])
                if merged is not None:
                    pending = merged
                    segment_candidates = segment_candidates[1:]
                else:
                    candidates.append(pending)
                    pending = None
            if segment_candidates:
                if pending is not None:
                    candidates.append(pending)
                pending = segment_candidates[-1]
                candidates.extend(segment_candidates[:-1])
        if pending is not None:
            candidates.append(pending)
        return candidates

    def _merge_if_tiny(self, left: ChunkCandidate, right: ChunkCandidate) -> ChunkCandidate | None:
        left_count = self.token_counter.count(left.content)
        right_count = self.token_counter.count(right.content)
        if left_count >= self.policy.min_tokens and right_count >= self.policy.min_tokens:
            return None
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
        for line in lines:
            trial = current + [line]
            rendered = _fenced(trial, language) if opening else "\n".join(trial)
            if current and self.token_counter.count(rendered) > self.policy.max_tokens:
                chunks.append(self._candidate([replace(source, text=_fenced(current, language) if opening else "\n".join(current))], "code_line_split", boundaries=()))
                current = [line]
            else:
                current = trial
        if current:
            chunks.append(self._candidate([replace(source, text=_fenced(current, language) if opening else "\n".join(current))], "code_line_split", boundaries=()))
        return self._force_safe(chunks)

    def _split_table(self, units: list[SemanticUnit]) -> list[ChunkCandidate]:
        source = units[0]
        lines = source.text.splitlines()
        if len(lines) < 3:
            return self._force_safe([self._candidate(units, "table_row_split", boundaries=())])
        header = lines[:2]
        chunks: list[ChunkCandidate] = []
        rows: list[str] = []
        for row in lines[2:]:
            trial = rows + [row]
            rendered = "\n".join([*header, *trial])
            if rows and self.token_counter.count(rendered) > self.policy.max_tokens:
                chunks.append(self._candidate([replace(source, text="\n".join([*header, *rows]))], "table_row_split", boundaries=()))
                rows = [row]
            else:
                rows = trial
        if rows:
            chunks.append(self._candidate([replace(source, text="\n".join([*header, *rows]))], "table_row_split", boundaries=()))
        return self._force_safe(chunks)

    def _force_safe(self, candidates: list[ChunkCandidate]) -> list[ChunkCandidate]:
        result: list[ChunkCandidate] = []
        for candidate in candidates:
            if self.token_counter.count(candidate.content) <= self.policy.max_tokens:
                result.append(self._with_count(candidate))
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
                    chunk_type="text" if candidate.chunk_type == "table" else candidate.chunk_type,
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
