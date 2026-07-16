from __future__ import annotations

import asyncio
import os

from .image_parser import ImageParser
from .models import DocumentBlock, DocumentFileType, ProcessingMode, SourceElementType
from .text_deduplicator import TextDeduplicator


class PDFParser:
    def __init__(self, *, image_parser: ImageParser) -> None:
        self.image_parser = image_parser
        self.deduplicator = TextDeduplicator()

    async def parse(self, *, content: bytes, filename: str, mime_type: str) -> list[DocumentBlock]:
        return await asyncio.to_thread(self._parse_sync, content, filename, mime_type)

    def _parse_sync(self, content: bytes, filename: str, mime_type: str) -> list[DocumentBlock]:
        import fitz

        try:
            document = fitz.open(stream=content, filetype="pdf")
        except Exception as exc:
            raise ValueError("pdf document could not be parsed") from exc
        try:
            if document.needs_pass:
                raise ValueError("encrypted pdf is not supported")
            max_pages = _int_env("DOCUMENT_MAX_PDF_PAGES", 200)
            if document.page_count > max_pages:
                raise ValueError(f"pdf document exceeds {max_pages} page limit")
            blocks: list[DocumentBlock] = []
            min_chars = _int_env("DOCUMENT_PDF_MIN_TEXT_CHARS", 50)
            for page_number, page in enumerate(document, start=1):
                text = _normalize(page.get_text("text"))
                if text:
                    blocks.append(DocumentBlock(
                        filename=filename, file_type=DocumentFileType.PDF, page_number=page_number, block_index=1,
                        text=text, processing_mode=ProcessingMode.PDF_TEXT,
                        source_element=SourceElementType.PDF_TEXT_LAYER,
                    ))
                if len(text) >= min_chars:
                    continue
                pixmap = page.get_pixmap(dpi=_int_env("DOCUMENT_RENDER_DPI", 150), alpha=False)
                try:
                    ocr_blocks = asyncio.run(self.image_parser.parse(
                        content=pixmap.tobytes("png"), filename=filename, mime_type="image/png", page_number=page_number,
                        file_type=DocumentFileType.PDF, processing_mode=ProcessingMode.PDF_OCR,
                        source_element=SourceElementType.PDF_RENDER,
                    ))
                except Exception:
                    if text:
                        continue
                    continue
                for ocr_block in ocr_blocks:
                    ocr_block.text = self.deduplicator.remove_overlapping_lines(
                        primary_text=text, supplemental_text=ocr_block.text
                    )
                    if ocr_block.text:
                        blocks.append(ocr_block)
            return blocks
        finally:
            document.close()


def _normalize(value: str) -> str:
    return "\n".join(line.strip() for line in value.splitlines() if line.strip())


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
