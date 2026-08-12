from __future__ import annotations

import asyncio
import os
import re
from statistics import median
from typing import Any, Sequence

from .image_parser import ImageParseArtifacts, ImageParser
from .models import (
    BlockStyleSignals,
    BoundingBox,
    DocumentBlock,
    DocumentBlockType,
    DocumentFileType,
    DocumentParsingProfile,
    OCRResult,
    PDFTextQualityMetadata,
    ParseWarning,
    ProcessingMode,
    SourceElementType,
    TextQualityAssessment,
)
from .text_deduplicator import TextDeduplicator
from .text_quality import assess_pdf_text
from .structured_ocr import StructuredOCRBlockBuilder
from .table_detection import (
    DetectedTable,
    SpatialToken,
    TableCandidateValidator,
    TableDetectionMethod,
    spatial_table_from_tokens,
)


class TableDetector:
    def detect(self, page: Any, *, text_blocks: Sequence[dict[str, Any]]) -> list[DetectedTable]:
        for method in (
            TableDetectionMethod.PYMUPDF_LINES,
            TableDetectionMethod.PYMUPDF_TEXT,
        ):
            tables = self._find_tables(page, method=method)
            valid = [table for table in tables if TableCandidateValidator().validate(table).accepted]
            if valid:
                return valid
        return [
            table
            for table in _spatial_tables(text_blocks)
            if TableCandidateValidator().validate(table).accepted
        ]

    def _find_tables(self, page: Any, *, method: TableDetectionMethod) -> list[DetectedTable]:
        try:
            kwargs = {} if method is TableDetectionMethod.PYMUPDF_LINES else {"strategy": "text"}
            result = page.find_tables(**kwargs)
        except Exception:
            return []
        tables: list[DetectedTable] = []
        for table in getattr(result, "tables", ()):
            rows = tuple(
                tuple((cell or "").strip() for cell in row)
                for row in (table.extract() or ())
            )
            tables.append(
                DetectedTable(
                    bbox=_bbox(table.bbox),
                    rows=rows,
                    header_rows=1 if rows else 0,
                    method=method,
                    confidence=0.95 if method is TableDetectionMethod.PYMUPDF_LINES else 0.85,
                )
            )
        return tables


class PDFParser:
    def __init__(self, *, image_parser: ImageParser) -> None:
        self.image_parser = image_parser
        self.deduplicator = TextDeduplicator()
        self.table_detector = TableDetector()
        self.structured_ocr_builder = StructuredOCRBlockBuilder()

    async def parse(
        self,
        *,
        content: bytes,
        filename: str,
        mime_type: str,
        profile: DocumentParsingProfile = DocumentParsingProfile.LEGACY_V2,
    ) -> list[DocumentBlock]:
        return await asyncio.to_thread(self._parse_sync, content, filename, mime_type, profile)

    async def page_count(self, *, content: bytes) -> int:
        return await asyncio.to_thread(_page_count, content)

    def _parse_sync(
        self,
        content: bytes,
        filename: str,
        mime_type: str,
        profile: DocumentParsingProfile,
    ) -> list[DocumentBlock]:
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
            if profile is DocumentParsingProfile.LEGACY_V2:
                return self._parse_legacy(document, filename)
            return self._parse_structured(document, filename)
        finally:
            document.close()

    def _parse_legacy(self, document: Any, filename: str) -> list[DocumentBlock]:
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
                        native=native_quality, ocr=None, selected="native",
                        reason="ocr_unavailable_or_empty", minimum_score=minimum_score,
                    )
                    native_block.warnings.append(ParseWarning(
                        code="pdf_ocr_unavailable_or_empty",
                        message="OCR produced no usable text; retained the native PDF text.",
                        page_number=page_number, source_element=SourceElementType.PDF_RENDER,
                    ))
                    _warn_if_low_quality(native_block, native_quality)
                    blocks.append(native_block)
                continue
            ocr_block, ocr_quality = max(ocr_candidates, key=lambda candidate: _quality_rank(candidate[1]))
            ocr_block.text = ocr_block.text.strip()
            if native_block is None:
                selected_block, selected_quality, selected, reason = ocr_block, ocr_quality, "ocr", "ocr_better"
            elif _quality_rank(ocr_quality) >= _quality_rank(native_quality):
                selected_block, selected_quality, selected = ocr_block, ocr_quality, "ocr"
                reason = "ocr_tie_preferred" if _quality_rank(ocr_quality) == _quality_rank(native_quality) else "ocr_better"
            else:
                selected_block, selected_quality, selected, reason = native_block, native_quality, "native", "native_better"
            selected_block.text_quality = _quality_metadata(
                native=native_quality, ocr=ocr_quality, selected=selected,
                reason=reason, minimum_score=minimum_score,
            )
            _warn_if_low_quality(selected_block, selected_quality)
            blocks.append(selected_block)
        return blocks

    def _parse_structured(self, document: Any, filename: str) -> list[DocumentBlock]:
        blocks: list[DocumentBlock] = []
        for page_number, page in enumerate(document, start=1):
            page_text = _normalize(page.get_text("text"))
            payload = page.get_text("dict", sort=True)
            text_blocks = [block for block in payload.get("blocks", ()) if block.get("type") == 0]
            native_blocks = self._structured_native_blocks(
                page=page,
                filename=filename,
                page_number=page_number,
                page_text=page_text,
                text_blocks=text_blocks,
            )
            native_quality = assess_pdf_text(page_text)
            minimum_score = _float_env("DOCUMENT_PDF_MIN_QUALITY_SCORE", 0.80)
            if native_blocks and native_quality.quality_sufficient:
                _apply_quality_metadata(
                    native_blocks,
                    native=native_quality,
                    ocr=None,
                    selected="native",
                    reason="native_quality_sufficient",
                    minimum_score=minimum_score,
                )
                blocks.extend(native_blocks)
                continue
            artifacts = self._extract_page_artifacts(page=page, filename=filename, page_number=page_number)
            ocr = artifacts.ocr
            if ocr is None and artifacts.final_text:
                ocr = _ocr_text_artifact(artifacts.final_text)
            ocr_blocks = self.structured_ocr_builder.build(
                ocr=ocr,
                filename=filename,
                page_number=page_number,
                page_width=float(page.rect.width),
                page_height=float(page.rect.height),
            ) if ocr is not None else []
            ocr_quality = assess_pdf_text(artifacts.final_text) if artifacts.final_text else None
            if not ocr_blocks or ocr_quality is None:
                if native_blocks:
                    _apply_quality_metadata(
                        native_blocks,
                        native=native_quality,
                        ocr=None,
                        selected="native",
                        reason="ocr_unavailable_or_empty",
                        minimum_score=minimum_score,
                    )
                    for block in native_blocks:
                        block.warnings.append(ParseWarning(
                            code="pdf_ocr_unavailable_or_empty",
                            message="OCR produced no usable text; retained the native PDF text.",
                            page_number=page_number,
                            source_element=SourceElementType.PDF_RENDER,
                        ))
                        _warn_if_low_quality(block, native_quality)
                    blocks.extend(native_blocks)
                continue
            if not native_blocks or _quality_rank(ocr_quality) >= _quality_rank(native_quality):
                selected_blocks = ocr_blocks
                selected_quality = ocr_quality
                selected = "ocr"
                reason = "ocr_better" if not native_blocks or _quality_rank(ocr_quality) > _quality_rank(native_quality) else "ocr_tie_preferred"
            else:
                selected_blocks = native_blocks
                selected_quality = native_quality
                selected = "native"
                reason = "native_better"
            _apply_quality_metadata(
                selected_blocks,
                native=native_quality,
                ocr=ocr_quality,
                selected=selected,
                reason=reason,
                minimum_score=minimum_score,
            )
            for block in selected_blocks:
                _warn_if_low_quality(block, selected_quality)
            blocks.extend(selected_blocks)
        return sorted(blocks, key=lambda block: (block.page_number, block.bbox.y0 if block.bbox else 0, block.bbox.x0 if block.bbox else 0))

    def _extract_page_artifacts(self, *, page: Any, filename: str, page_number: int):
        try:
            pixmap = page.get_pixmap(dpi=_int_env("DOCUMENT_RENDER_DPI", 150), alpha=False)
            return asyncio.run(self.image_parser.extract_artifacts(
                content=pixmap.tobytes("png"),
                filename=filename,
                mime_type="image/png",
                page_number=page_number,
                file_type=DocumentFileType.PDF,
                source_element=SourceElementType.PDF_RENDER,
            ))
        except Exception:
            return _empty_artifacts()

    def _structured_native_blocks(
        self,
        *,
        page: Any,
        filename: str,
        page_number: int,
        page_text: str,
        text_blocks: Sequence[dict[str, Any]],
    ) -> list[DocumentBlock]:
        tables = self.table_detector.detect(page, text_blocks=text_blocks)
        native_blocks = [
            _table_block(filename=filename, page_number=page_number, table=table, page_text=page_text)
            for table in tables
        ]
        body_font = _body_font_size(text_blocks)
        for block_index, block in enumerate(text_blocks, start=1):
            bbox = _bbox(block.get("bbox", (0, 0, 0, 0)))
            if any(_overlap_ratio(bbox, table.bbox) >= 0.5 for table in tables):
                continue
            text, spans = _block_text_and_spans(block)
            if not text:
                continue
            font_size = median([float(span.get("size", 0)) for span in spans]) if spans else None
            flags = [int(span.get("flags", 0)) for span in spans]
            fonts = [str(span.get("font", "")) for span in spans]
            heading_level, confidence = _heading_level(text, font_size, body_font, flags)
            if heading_level is not None:
                block_type = DocumentBlockType.HEADING
            elif _looks_like_code(text, fonts):
                block_type, confidence = DocumentBlockType.CODE, 0.72
            else:
                block_type, confidence = DocumentBlockType.PARAGRAPH, 0.8
            source_start, source_end = _source_span(page_text, text)
            native_blocks.append(DocumentBlock(
                filename=filename,
                file_type=DocumentFileType.PDF,
                page_number=page_number,
                block_index=block_index,
                text=text,
                processing_mode=ProcessingMode.PDF_TEXT,
                source_element=SourceElementType.PDF_TEXT_LAYER,
                block_type=block_type,
                heading_level=heading_level,
                bbox=bbox,
                reading_order=block_index,
                structure_confidence=confidence,
                style_signals=BlockStyleSignals(
                    font_size=font_size,
                    font_size_ratio=(font_size / body_font if font_size and body_font else None),
                    is_bold=any(flag & 16 for flag in flags),
                    is_monospace=any(_is_monospace(font) for font in fonts),
                ),
                source_char_start=source_start,
                source_char_end=source_end,
            ))
        return native_blocks


def _block_text_and_spans(block: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    spans = [span for line in block.get("lines", ()) for span in line.get("spans", ()) if span.get("text", "").strip()]
    text = "\n".join(
        "".join(span.get("text", "") for span in line.get("spans", ())).strip()
        for line in block.get("lines", ())
        if "".join(span.get("text", "") for span in line.get("spans", ())).strip()
    )
    return text, spans


def _body_font_size(blocks: Sequence[dict[str, Any]]) -> float:
    values: list[float] = []
    weighted: list[float] = []
    for block in blocks:
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                size = float(span.get("size", 0))
                text = str(span.get("text", ""))
                if size > 0 and text.strip():
                    values.append(size)
                    weighted.extend([size] * max(1, len(text)))
    return median(weighted or values or [11.0])


def _heading_level(text: str, font_size: float | None, body_font: float, flags: list[int]) -> tuple[int | None, float]:
    if not text or len(text) > 160 or (text[-1:] in ".?!。！？" and len(text) > 70):
        return None, 0.0
    ratio = (font_size or body_font) / (body_font or 1.0)
    numbered = bool(re.match(r"^(?:\d+(?:\.\d+)*|chapter\s+\d+|第[一二三四五六七八九十]+章|[一二三四五六七八九十]+、)", text, re.I))
    bold = any(flag & 16 for flag in flags)
    if not ((ratio >= 1.18) or (bold and ratio >= 1.05) or numbered):
        return None, 0.0
    level = 1 if ratio >= 1.6 else 2 if ratio >= 1.35 else 3
    return min(level, 6), min(1.0, 0.55 + max(0.0, ratio - 1.0) * 0.35 + (0.1 if numbered else 0.0))


def _looks_like_code(text: str, fonts: Sequence[str]) -> bool:
    if _is_monospace(" ".join(fonts)):
        return True
    lines = text.splitlines()
    if len(lines) < 2 or not any(line.startswith(("    ", "\t")) for line in lines):
        return False
    punctuation = sum(text.count(mark) for mark in ("{", "}", "=>", "def ", "class ", ";"))
    return punctuation >= 2


def _is_monospace(font: str) -> bool:
    normalized = font.casefold()
    return any(value in normalized for value in ("courier", "mono", "consolas", "menlo"))


def _table_block(*, filename: str, page_number: int, table: DetectedTable, page_text: str) -> DocumentBlock:
    header = table.rows[0] if table.rows else ()
    lines = ["| " + " | ".join(row) + " |" for row in table.rows]
    separator = "| " + " | ".join("---" for _ in header) + " |"
    text = "\n".join([lines[0], separator, *lines[1:]]) if lines else ""
    source_start, source_end = _source_span(page_text, " ".join(cell for row in table.rows for cell in row))
    return DocumentBlock(
        filename=filename,
        file_type=DocumentFileType.PDF,
        page_number=page_number,
        block_index=1,
        text=text,
        processing_mode=ProcessingMode.PDF_TEXT,
        source_element=SourceElementType.PDF_TEXT_LAYER,
        block_type=DocumentBlockType.TABLE,
        bbox=table.bbox,
        reading_order=0,
        structure_confidence=table.confidence,
        table_header_rows=table.header_rows,
        source_char_start=source_start,
        source_char_end=source_end,
    )


def _spatial_tables(blocks: Sequence[dict[str, Any]]) -> list[DetectedTable]:
    cells: list[SpatialToken] = []
    for block in blocks:
        block_bbox = _bbox(block.get("bbox", (0, 0, 0, 0)))
        lines = block.get("lines", ())
        for line in lines:
            spans = [span for span in line.get("spans", ()) if str(span.get("text", "")).strip()]
            if not spans:
                continue
            text = "".join(str(span.get("text", "")) for span in spans).strip()
            line_bbox = _bbox(line.get("bbox", block_bbox))
            if text:
                cells.append(SpatialToken(text=text, bbox=line_bbox))
    candidate = spatial_table_from_tokens(tuple(cells))
    return [candidate] if candidate is not None else []


def _bbox(value: Any) -> BoundingBox:
    x0, y0, x1, y1 = value
    return BoundingBox(x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1))


def _overlap_ratio(left: BoundingBox, right: BoundingBox) -> float:
    x0, y0 = max(left.x0, right.x0), max(left.y0, right.y0)
    x1, y1 = min(left.x1, right.x1), min(left.y1, right.y1)
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area = max(1.0, (left.x1 - left.x0) * (left.y1 - left.y0))
    return intersection / area


def _source_span(source: str, value: str) -> tuple[int | None, int | None]:
    normalized_source = " ".join(source.split())
    normalized_value = " ".join(value.split())
    if not normalized_value:
        return None, None
    index = normalized_source.find(normalized_value)
    if index < 0:
        return None, None
    return index, index + len(normalized_value)


def _normalize(value: str) -> str:
    return "\n".join(line.strip() for line in value.splitlines() if line.strip())


def _native_block(*, filename: str, page_number: int, text: str) -> DocumentBlock:
    return DocumentBlock(
        filename=filename, file_type=DocumentFileType.PDF, page_number=page_number, block_index=1,
        text=text, processing_mode=ProcessingMode.PDF_TEXT, source_element=SourceElementType.PDF_TEXT_LAYER,
    )


def _quality_rank(assessment: TextQualityAssessment) -> tuple[bool, bool, float]:
    return assessment.hard_gate_pass, assessment.quality_sufficient, assessment.soft_score


def _quality_metadata(*, native: TextQualityAssessment, ocr: TextQualityAssessment | None, selected: str, reason: str, minimum_score: float) -> PDFTextQualityMetadata:
    return PDFTextQualityMetadata(native=native, ocr=ocr, selected=selected, reason=reason, minimum_score=minimum_score)


def _apply_quality_metadata(
    blocks: Sequence[DocumentBlock],
    *,
    native: TextQualityAssessment,
    ocr: TextQualityAssessment | None,
    selected: str,
    reason: str,
    minimum_score: float,
) -> None:
    metadata = _quality_metadata(
        native=native,
        ocr=ocr,
        selected=selected,
        reason=reason,
        minimum_score=minimum_score,
    )
    for block in blocks:
        block.text_quality = metadata


def _empty_artifacts() -> ImageParseArtifacts:
    return ImageParseArtifacts(
        ocr=None,
        final_text="",
        vision_status=None,
        vision_confidence=None,
        warnings=("render_or_ocr_unavailable",),
    )


def _ocr_text_artifact(text: str) -> OCRResult:
    normalized = text.strip()
    return OCRResult(
        text=normalized,
        confidence=None,
        word_count=len(normalized.split()),
        text_char_count=len(normalized),
    )


def _warn_if_low_quality(block: DocumentBlock, assessment: TextQualityAssessment) -> None:
    if assessment.quality_sufficient:
        return
    block.warnings.append(ParseWarning(
        code="pdf_text_quality_below_threshold",
        message="The best available PDF text remains below the configured quality threshold.",
        page_number=block.page_number, source_element=block.source_element,
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
