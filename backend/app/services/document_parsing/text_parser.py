from __future__ import annotations

import hashlib
import re

from backend.app.services.document_parsing.models import (
    DocumentBlock,
    DocumentBlockType,
    DocumentFileType,
    DocumentParseResult,
    ParseStatus,
    ProcessingMode,
    SourceElementType,
)


_MARKDOWN_MIME_TYPES = {"text/markdown", "application/markdown"}
_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")


class StructuredTextParser:
    parser_version = "document-parser-v4.1"

    def parse(
        self,
        *,
        content: bytes,
        filename: str,
        mime_type: str,
    ) -> DocumentParseResult:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("text document must be utf-8") from exc
        is_markdown = mime_type.lower() in _MARKDOWN_MIME_TYPES or filename.lower().endswith(tuple(_MARKDOWN_SUFFIXES))
        blocks = _markdown_blocks(text, filename=filename) if is_markdown else _plain_text_blocks(text, filename=filename)
        return DocumentParseResult(
            status=ParseStatus.SUCCESS if blocks else ParseStatus.FAILED,
            filename=filename,
            file_type=DocumentFileType.TEXT,
            mime_type=mime_type,
            content_sha256=hashlib.sha256(content).hexdigest(),
            parser_version=self.parser_version,
            page_count=1 if blocks else 0,
            block_count=len(blocks),
            blocks=blocks,
            processing_time_ms=0,
        )


def _markdown_blocks(text: str, *, filename: str) -> list[DocumentBlock]:
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    blocks: list[DocumentBlock] = []
    index = 0
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            index += 1
            continue
        start = offsets[index]
        if stripped.startswith("```"):
            end_index = index + 1
            while end_index < len(lines) and lines[end_index].strip() != "```":
                end_index += 1
            if end_index < len(lines):
                end_index += 1
            _append_block(blocks, filename, text[start:offsets[end_index] if end_index < len(lines) else len(text)], DocumentBlockType.CODE, start, offsets[end_index] if end_index < len(lines) else len(text), "markdown")
            index = end_index
            continue
        if stripped.startswith("#") and len(stripped) > 1 and stripped[1] in {" ", "#"}:
            heading = stripped.lstrip("#").strip()
            _append_block(blocks, filename, heading, DocumentBlockType.HEADING, start, offsets[index] + len(raw), "markdown", heading_level=len(stripped) - len(stripped.lstrip("#")))
            index += 1
            continue
        if _LIST_ITEM.match(raw):
            _append_block(blocks, filename, stripped, DocumentBlockType.LIST_ITEM, start, offsets[index] + len(raw), "markdown")
            index += 1
            continue
        if index + 1 < len(lines) and "|" in raw and re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1]):
            end_index = index + 2
            while end_index < len(lines) and lines[end_index].strip() and "|" in lines[end_index]:
                end_index += 1
            end = offsets[end_index] if end_index < len(lines) else len(text)
            _append_block(blocks, filename, text[start:end].strip(), DocumentBlockType.TABLE, start, end, "markdown")
            index = end_index
            continue
        end_index = index + 1
        while end_index < len(lines):
            next_stripped = lines[end_index].strip()
            if not next_stripped or next_stripped.startswith("```") or next_stripped.startswith("#") or _LIST_ITEM.match(lines[end_index]):
                break
            if end_index + 1 < len(lines) and "|" in lines[end_index] and re.match(r"^\s*\|?\s*:?-{3,}", lines[end_index + 1]):
                break
            end_index += 1
        end = offsets[end_index] if end_index < len(lines) else len(text)
        _append_block(blocks, filename, text[start:end].strip(), DocumentBlockType.PARAGRAPH, start, end, "markdown")
        index = end_index
    return blocks


def _plain_text_blocks(text: str, *, filename: str) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", text, flags=re.DOTALL):
        _append_block(blocks, filename, match.group(0), DocumentBlockType.PARAGRAPH, match.start(), match.end(), "plain_text")
    return blocks


def _append_block(
    blocks: list[DocumentBlock],
    filename: str,
    value: str,
    block_type: DocumentBlockType,
    start: int,
    end: int,
    source_format: str,
    *,
    heading_level: int | None = None,
) -> None:
    text = value.strip()
    if not text:
        return
    number = len(blocks) + 1
    blocks.append(
        DocumentBlock(
            filename=filename,
            file_type=DocumentFileType.TEXT,
            page_number=1,
            block_index=number,
            text=text,
            processing_mode=ProcessingMode.TEXT_NATIVE,
            source_element=SourceElementType.TEXT_FILE,
            block_type=block_type,
            heading_level=heading_level,
            reading_order=number,
            structure_confidence=1.0,
            source_char_start=start,
            source_char_end=end,
            source_format=source_format,
        )
    )
