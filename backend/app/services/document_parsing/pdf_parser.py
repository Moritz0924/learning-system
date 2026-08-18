from __future__ import annotations

import asyncio
import os

from .image_parser import ImageParser
from .models import (
    DocumentBlock,
    DocumentFileType,
    PDFTextQualityMetadata,
    ParseWarning,
    ProcessingMode,
    SourceElementType,
    TextQualityAssessment,
)
from .text_quality import assess_pdf_text


class PDFParser:
    def __init__(self, *, image_parser: ImageParser) -> None:
        self.image_parser = image_parser

    async def parse(self, *, content: bytes, filename: str, mime_type: str) -> list[DocumentBlock]:
        return await asyncio.to_thread(self._parse_sync, content, filename, mime_type)

    async def page_count(self, *, content: bytes) -> int:
        return await asyncio.to_thread(_page_count, content)

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
            for page_number, page in enumerate(document, start=1):
                text = _normalize(page.get_text("text"))
                native_quality = assess_pdf_text(text)
                native_block = _native_block(filename=filename, page_number=page_number, text=text) if text else None
                minimum_score = _float_env("DOCUMENT_PDF_MIN_QUALITY_SCORE", 0.80)
                if native_quality.quality_sufficient and native_block is not None:
                    native_block.text_quality = _quality_metadata(
                        native=native_quality,
                        ocr=None,
                        selected="native",
                        reason="native_quality_sufficient",
                        minimum_score=minimum_score,
                    )
                    blocks.append(native_block)
                    continue
                try:
                    pixmap = page.get_pixmap(dpi=_int_env("DOCUMENT_RENDER_DPI", 150), alpha=False)
                    ocr_blocks = asyncio.run(self.image_parser.parse(
                        content=pixmap.tobytes("png"), filename=filename, mime_type="image/png", page_number=page_number,
                        file_type=DocumentFileType.PDF, processing_mode=ProcessingMode.PDF_OCR,
                        source_element=SourceElementType.PDF_RENDER,
                    ))
                except Exception:
                    ocr_blocks = []
                ocr_candidates = [
                    (ocr_block, assess_pdf_text(ocr_block.text))
                    for ocr_block in ocr_blocks
                    if ocr_block.text.strip()
                ]
                if not ocr_candidates:
                    if native_block is not None:
                        native_block.text_quality = _quality_metadata(
                            native=native_quality,
                            ocr=None,
                            selected="native",
                            reason="ocr_unavailable_or_empty",
                            minimum_score=minimum_score,
                        )
                        native_block.warnings.append(ParseWarning(
                            code="pdf_ocr_unavailable_or_empty",
                            message="OCR produced no usable text; retained the native PDF text.",
                            page_number=page_number,
                            source_element=SourceElementType.PDF_RENDER,
                        ))
                        _warn_if_low_quality(native_block, native_quality)
                        blocks.append(native_block)
                    continue
                ocr_block, ocr_quality = max(
                    ocr_candidates,
                    key=lambda candidate: _quality_rank(candidate[1]),
                )
                ocr_block.text = ocr_block.text.strip()
                if native_block is None:
                    selected_block = ocr_block
                    selected_quality = ocr_quality
                    selected = "ocr"
                    reason = "ocr_better"
                else:
                    native_rank = _quality_rank(native_quality)
                    ocr_rank = _quality_rank(ocr_quality)
                    if ocr_rank >= native_rank:
                        selected_block = ocr_block
                        selected_quality = ocr_quality
                        selected = "ocr"
                        reason = "ocr_tie_preferred" if ocr_rank == native_rank else "ocr_better"
                    else:
                        selected_block = native_block
                        selected_quality = native_quality
                        selected = "native"
                        reason = "native_better"
                selected_block.text_quality = _quality_metadata(
                    native=native_quality,
                    ocr=ocr_quality,
                    selected=selected,
                    reason=reason,
                    minimum_score=minimum_score,
                )
                _warn_if_low_quality(selected_block, selected_quality)
                blocks.append(selected_block)
            return blocks
        finally:
            document.close()


def _normalize(value: str) -> str:
    return "\n".join(line.strip() for line in value.splitlines() if line.strip())


def _native_block(*, filename: str, page_number: int, text: str) -> DocumentBlock:
    return DocumentBlock(
        filename=filename,
        file_type=DocumentFileType.PDF,
        page_number=page_number,
        block_index=1,
        text=text,
        processing_mode=ProcessingMode.PDF_TEXT,
        source_element=SourceElementType.PDF_TEXT_LAYER,
    )


def _quality_rank(assessment: TextQualityAssessment) -> tuple[bool, bool, float]:
    return assessment.hard_gate_pass, assessment.quality_sufficient, assessment.soft_score


def _quality_metadata(
    *,
    native: TextQualityAssessment,
    ocr: TextQualityAssessment | None,
    selected: str,
    reason: str,
    minimum_score: float,
) -> PDFTextQualityMetadata:
    return PDFTextQualityMetadata(
        native=native,
        ocr=ocr,
        selected=selected,
        reason=reason,
        minimum_score=minimum_score,
    )


def _warn_if_low_quality(block: DocumentBlock, assessment: TextQualityAssessment) -> None:
    if assessment.quality_sufficient:
        return
    block.warnings.append(ParseWarning(
        code="pdf_text_quality_below_threshold",
        message="The best available PDF text remains below the configured quality threshold.",
        page_number=block.page_number,
        source_element=block.source_element,
    ))


def _page_count(content: bytes) -> int:
    import fitz

    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise ValueError("pdf document could not be parsed") from exc
    try:
        if document.needs_pass:
            raise ValueError("encrypted pdf is not supported")
        return document.page_count
    finally:
        document.close()


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if 0.0 <= value <= 1.0 else default
