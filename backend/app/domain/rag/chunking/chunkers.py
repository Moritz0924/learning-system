from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Mapping, Protocol

from .domain import ChunkDraft, ChunkPolicy, ChunkType
from .normalization import normalize_chunk_text


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_TABLE_SEPARATOR = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_FENCE_OPENING = re.compile(r"^ {0,3}(?P<delimiter>`{3,}|~{3,})(?P<info>.*)$")
_FENCE_CLOSING = re.compile(r"^ {0,3}(?P<delimiter>`{3,}|~{3,})\s*$")
_BOUNDARY_CHARACTERS = frozenset(".?!;。！？；")


class Chunker(Protocol):
    def chunk(
        self,
        content: str,
        *,
        heading_path: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> list[ChunkDraft]: ...


class TextChunker:
    def __init__(self, policy: ChunkPolicy, *, chunk_type: ChunkType = ChunkType.TEXT) -> None:
        self.policy = policy
        self.chunk_type = chunk_type

    def chunk(
        self,
        content: str,
        *,
        heading_path: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> list[ChunkDraft]:
        return [
            ChunkDraft(
                part,
                self.chunk_type,
                heading_path=heading_path,
                metadata=metadata or {},
            )
            for part in _split_text(content, self.policy)
        ]


class CodeChunker:
    def __init__(self, policy: ChunkPolicy) -> None:
        self.policy = policy

    def chunk(
        self,
        content: str,
        *,
        heading_path: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> list[ChunkDraft]:
        normalized = normalize_chunk_text(content)
        lines = normalized.splitlines()
        opening = _parse_opening_fence(lines[0]) if lines else None
        if (
            opening is None
            or len(lines) < 2
            or not _is_matching_closing_fence(lines[-1], opening[0])
        ):
            return TextChunker(self.policy, chunk_type=ChunkType.CODE).chunk(
                normalized,
                heading_path=heading_path,
                metadata=metadata,
            )
        opener = lines[0].strip()
        closer = lines[-1].strip()
        body = "\n".join(lines[1:-1])
        wrapper_chars = len(opener) + len(closer) + 2
        if wrapper_chars + self.policy.min_chars > self.policy.max_chars:
            return TextChunker(self.policy, chunk_type=ChunkType.CODE).chunk(
                normalized,
                heading_path=heading_path,
                metadata=metadata,
            )
        inner_policy = replace(
            self.policy,
            target_chars=max(self.policy.min_chars, self.policy.target_chars - wrapper_chars),
            max_chars=self.policy.max_chars - wrapper_chars,
        )
        base_metadata = dict(metadata or {})
        language = opening[1]
        if language:
            base_metadata["code_language"] = language
        return [
            ChunkDraft(
                f"{opener}\n{part}\n{closer}",
                ChunkType.CODE,
                heading_path=heading_path,
                metadata=base_metadata,
            )
            for part in _split_text(body, inner_policy)
        ]


class TableChunker:
    def __init__(self, policy: ChunkPolicy) -> None:
        self.policy = policy

    def chunk(
        self,
        content: str,
        *,
        heading_path: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> list[ChunkDraft]:
        normalized = normalize_chunk_text(content)
        lines = [line for line in normalized.splitlines() if line.strip()]
        if len(lines) < 3 or not _TABLE_SEPARATOR.match(lines[1]):
            return TextChunker(self.policy, chunk_type=ChunkType.TABLE).chunk(
                normalized,
                heading_path=heading_path,
                metadata=metadata,
            )
        header = lines[:2]
        rows = lines[2:]
        if len("\n".join(header)) + 1 >= self.policy.max_chars:
            return TextChunker(self.policy, chunk_type=ChunkType.TABLE).chunk(
                normalized,
                heading_path=heading_path,
                metadata=metadata,
            )
        groups = _group_table_rows(header, rows, self.policy)
        return [
            ChunkDraft(
                "\n".join([*header, *group]),
                ChunkType.TABLE,
                heading_path=heading_path,
                metadata=metadata or {},
            )
            for group in groups
        ]


class MarkdownChunker:
    def __init__(self, policy: ChunkPolicy) -> None:
        self.policy = policy
        self.text_chunker = TextChunker(policy, chunk_type=ChunkType.MARKDOWN)
        self.code_chunker = CodeChunker(policy)
        self.table_chunker = TableChunker(policy)

    def chunk(
        self,
        content: str,
        *,
        heading_path: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> list[ChunkDraft]:
        normalized = normalize_chunk_text(content)
        lines = normalized.splitlines()
        path = list(heading_path)
        buffer: list[str] = []
        chunks: list[ChunkDraft] = []

        def flush_text() -> None:
            text = normalize_chunk_text("\n".join(buffer))
            buffer.clear()
            if text:
                chunks.extend(
                    self.text_chunker.chunk(
                        text,
                        heading_path=tuple(path),
                        metadata=metadata,
                    )
                )

        index = 0
        while index < len(lines):
            line = lines[index]
            heading_match = _HEADING.match(line)
            if heading_match:
                flush_text()
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                path[:] = path[: level - 1]
                path.append(title)
                buffer.append(line)
                index += 1
                continue
            opening = _parse_opening_fence(line)
            if opening is not None:
                flush_text()
                fenced = [line]
                index += 1
                while index < len(lines):
                    fenced.append(lines[index])
                    if _is_matching_closing_fence(lines[index], opening[0]):
                        index += 1
                        break
                    index += 1
                chunks.extend(
                    self.code_chunker.chunk(
                        "\n".join(fenced),
                        heading_path=tuple(path),
                        metadata=metadata,
                    )
                )
                continue
            if index + 1 < len(lines) and "|" in line and _TABLE_SEPARATOR.match(lines[index + 1]):
                flush_text()
                table = [line, lines[index + 1]]
                index += 2
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    table.append(lines[index])
                    index += 1
                chunks.extend(
                    self.table_chunker.chunk(
                        "\n".join(table),
                        heading_path=tuple(path),
                        metadata=metadata,
                    )
                )
                continue
            buffer.append(line)
            index += 1
        flush_text()
        return chunks


def _parse_opening_fence(line: str) -> tuple[str, str] | None:
    match = _FENCE_OPENING.fullmatch(line)
    if match is None:
        return None
    delimiter = match.group("delimiter")
    info = match.group("info").strip()
    if delimiter[0] == "`" and "`" in info:
        return None
    return delimiter, info


def _is_matching_closing_fence(line: str, opening_delimiter: str) -> bool:
    match = _FENCE_CLOSING.fullmatch(line)
    if match is None:
        return False
    delimiter = match.group("delimiter")
    return (
        delimiter[0] == opening_delimiter[0]
        and len(delimiter) >= len(opening_delimiter)
    )


def _split_text(content: str, policy: ChunkPolicy) -> list[str]:
    normalized = normalize_chunk_text(content)
    if not normalized:
        return []
    if len(normalized) <= policy.max_chars:
        return [normalized]
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        remaining = len(normalized) - start
        if remaining <= policy.max_chars:
            chunks.append(normalized[start:].strip())
            break
        hard_end = min(
            start + policy.max_chars,
            len(normalized) - policy.min_chars + policy.overlap_chars,
        )
        target_end = min(start + policy.target_chars, hard_end)
        end = _choose_boundary(normalized, start, target_end, hard_end, policy)
        part = normalized[start:end].strip()
        while (
            len(part) > policy.overlap_chars
            and part[-policy.overlap_chars].isspace()
            and end < hard_end
        ):
            end += 1
            part = normalized[start:end].strip()
        if not part:
            end = min(start + policy.target_chars, hard_end)
            part = normalized[start:end]
        chunks.append(part)
        raw_part = normalized[start:end]
        content_end = start + len(raw_part.rstrip())
        next_start = max(start + 1, content_end - policy.overlap_chars)
        start = next_start
    return chunks


def _choose_boundary(
    text: str,
    start: int,
    target_end: int,
    hard_end: int,
    policy: ChunkPolicy,
) -> int:
    earliest = min(target_end, start + policy.min_chars)
    for position in range(target_end, earliest - 1, -1):
        if _is_boundary(text, position):
            return position
    for position in range(target_end + 1, hard_end + 1):
        if _is_boundary(text, position):
            return position
    return target_end


def _is_boundary(text: str, position: int) -> bool:
    if position <= 0 or position > len(text):
        return False
    previous = text[position - 1]
    return previous.isspace() or previous in _BOUNDARY_CHARACTERS


def _group_table_rows(
    header: list[str],
    rows: list[str],
    policy: ChunkPolicy,
) -> list[list[str]]:
    header_size = len("\n".join(header))
    if header_size >= policy.max_chars:
        return [rows]
    row_capacity = policy.max_chars - header_size - 1
    bounded_rows = [
        piece
        for row in rows
        for piece in _split_fixed_width(row, row_capacity)
    ]
    groups: list[list[str]] = []
    current: list[str] = []
    for row in bounded_rows:
        candidate = "\n".join([*header, *current, row])
        if current and len(candidate) > policy.target_chars:
            groups.append(current)
            current = _overlap_table_rows(current, policy.overlap_chars)
            candidate = "\n".join([*header, *current, row])
        if current and len(candidate) > policy.max_chars:
            groups.append(current)
            current = []
        current.append(row)
    if current:
        groups.append(current)
    return groups or [[]]


def _overlap_table_rows(rows: list[str], overlap_chars: int) -> list[str]:
    if overlap_chars <= 0:
        return []
    selected: list[str] = []
    size = 0
    for row in reversed(rows):
        row_size = len(row) + (1 if selected else 0)
        if size + row_size > overlap_chars:
            break
        selected.append(row)
        size += row_size
    return list(reversed(selected))


def _split_fixed_width(value: str, width: int) -> list[str]:
    if width <= 0:
        return [value]
    return [value[index : index + width] for index in range(0, len(value), width)] or [""]
