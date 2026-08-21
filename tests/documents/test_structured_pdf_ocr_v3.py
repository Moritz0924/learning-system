from __future__ import annotations

import asyncio

import fitz

from backend.app.services.document_parsing.models import (
    DocumentBlockType,
    DocumentParsingProfile,
    OCRResult,
    OCRWord,
    ProcessingMode,
)
from backend.app.services.document_parsing.parser import DocumentParser


def _pdf_with_text(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 550, 750), text)
    content = document.tobytes()
    document.close()
    return content


def _ocr_result(text: str, *, with_boxes: bool) -> OCRResult:
    words = [
        OCRWord(
            text=value,
            confidence=0.98,
            left=index * 60 if with_boxes else None,
            top=20 if with_boxes else None,
            width=50 if with_boxes else None,
            height=12 if with_boxes else None,
        )
        for index, value in enumerate(text.split())
    ]
    return OCRResult(
        text=text,
        confidence=0.98,
        word_count=len(words),
        text_char_count=len(text),
        words=words,
    )


def test_structured_pdf_uses_native_blocks_without_ocr_when_page_quality_is_good() -> None:
    class OCRMustNotRun:
        async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
            raise AssertionError("OCR must not run for a sufficient native page")

    result = asyncio.run(
        DocumentParser(ocr_service=OCRMustNotRun()).parse_document(
            content=_pdf_with_text("native structured page text " * 30),
            filename="native.pdf",
            mime_type="application/pdf",
            profile=DocumentParsingProfile.STRUCTURED_V3,
        )
    )

    assert result.parser_version == "document-parser-v4.1"
    assert result.blocks
    assert all(block.processing_mode is ProcessingMode.PDF_TEXT for block in result.blocks)
    assert all(block.text_quality.selected == "native" for block in result.blocks)
    assert all(block.text_quality.reason == "native_quality_sufficient" for block in result.blocks)


def test_structured_pdf_selects_structured_ocr_blocks_once_when_native_page_is_poor() -> None:
    ocr_text = "reliable OCR lesson content with spatial words " * 12

    class OCRWithBoxes:
        def __init__(self) -> None:
            self.calls = 0

        async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
            self.calls += 1
            return _ocr_result(ocr_text, with_boxes=True)

    ocr = OCRWithBoxes()
    result = asyncio.run(
        DocumentParser(ocr_service=ocr).parse_document(
            content=_pdf_with_text("brief title"),
            filename="poor-native.pdf",
            mime_type="application/pdf",
            profile=DocumentParsingProfile.STRUCTURED_V3,
        )
    )

    assert ocr.calls == 1
    assert result.blocks
    assert all(block.processing_mode is ProcessingMode.PDF_OCR for block in result.blocks)
    assert all(block.block_type is DocumentBlockType.PARAGRAPH for block in result.blocks)
    assert all(block.bbox is not None for block in result.blocks)
    assert all(block.text_quality.selected == "ocr" for block in result.blocks)
    assert all(block.text_quality.reason == "ocr_better" for block in result.blocks)


def test_structured_scanned_pdf_supports_ocr_without_word_boxes() -> None:
    scanned_text = "scanned PDF page with readable OCR content " * 12

    class OCRWithoutBoxes:
        async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
            return _ocr_result(scanned_text, with_boxes=False)

    document = fitz.open()
    page = document.new_page()
    page.draw_rect(page.rect, color=(0, 0, 0), fill=(1, 1, 1))
    content = document.tobytes()
    document.close()
    result = asyncio.run(
        DocumentParser(ocr_service=OCRWithoutBoxes()).parse_document(
            content=content,
            filename="scanned.pdf",
            mime_type="application/pdf",
            profile=DocumentParsingProfile.STRUCTURED_V3,
        )
    )

    assert len(result.blocks) == 1
    assert result.blocks[0].processing_mode is ProcessingMode.PDF_OCR
    assert result.blocks[0].block_type is DocumentBlockType.PARAGRAPH
    assert result.blocks[0].bbox is None
    assert result.blocks[0].text_quality.selected == "ocr"


def test_structured_pdf_retains_low_quality_native_blocks_when_ocr_is_unavailable() -> None:
    class OCRUnavailable:
        async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
            raise RuntimeError("provider unavailable")

    result = asyncio.run(
        DocumentParser(ocr_service=OCRUnavailable()).parse_document(
            content=_pdf_with_text("brief title"),
            filename="fallback.pdf",
            mime_type="application/pdf",
            profile=DocumentParsingProfile.STRUCTURED_V3,
        )
    )

    assert result.blocks
    assert all(block.processing_mode is ProcessingMode.PDF_TEXT for block in result.blocks)
    assert all(block.text_quality.selected == "native" for block in result.blocks)
    assert all(block.text_quality.reason == "ocr_unavailable_or_empty" for block in result.blocks)
    assert all("pdf_ocr_unavailable_or_empty" in [warning.code for warning in block.warnings] for block in result.blocks)


def test_structured_pdf_keeps_better_native_blocks_when_both_candidates_are_poor() -> None:
    class WorseOCR:
        async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
            return _ocr_result("aaaa " * 60, with_boxes=True)

    result = asyncio.run(
        DocumentParser(ocr_service=WorseOCR()).parse_document(
            content=_pdf_with_text(("alpha " * 9) + "+++++"),
            filename="both-poor.pdf",
            mime_type="application/pdf",
            profile=DocumentParsingProfile.STRUCTURED_V3,
        )
    )

    assert result.blocks
    assert all(block.processing_mode is ProcessingMode.PDF_TEXT for block in result.blocks)
    assert all(block.text_quality.selected == "native" for block in result.blocks)
    assert all(block.text_quality.reason == "native_better" for block in result.blocks)
