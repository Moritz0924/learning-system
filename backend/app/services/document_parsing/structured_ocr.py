from __future__ import annotations

from statistics import median

from .models import (
    BoundingBox,
    DocumentBlock,
    DocumentBlockType,
    DocumentFileType,
    OCRResult,
    OCRWord,
    ProcessingMode,
    SourceElementType,
)
from .table_detection import (
    SpatialToken,
    TableCandidateValidator,
    spatial_table_from_tokens,
)


class StructuredOCRBlockBuilder:
    def build(
        self,
        *,
        ocr: OCRResult,
        filename: str,
        page_number: int,
        page_width: float,
        page_height: float,
    ) -> list[DocumentBlock]:
        words = [word for word in ocr.words if _has_bbox(word)]
        if not words:
            return [_paragraph(
                filename=filename,
                page_number=page_number,
                index=1,
                text=ocr.text.strip(),
                bbox=None,
                confidence=ocr.confidence,
            )] if ocr.text.strip() else []
        tokens = tuple(SpatialToken(text=word.text, bbox=_word_bbox(word)) for word in words)
        table = spatial_table_from_tokens(tokens)
        validation = TableCandidateValidator().validate(table)
        if table is not None and validation.accepted:
            return [_table(
                filename=filename,
                page_number=page_number,
                table=table,
                page_width=page_width,
                page_height=page_height,
                confidence=ocr.confidence,
                structure_confidence=validation.confidence,
            )]
        rows = _spatial_rows(words)
        return [
            _paragraph(
                filename=filename,
                page_number=page_number,
                index=index,
                text=" ".join(word.text for word in row),
                bbox=_row_bbox(row, page_width=page_width, page_height=page_height),
                confidence=ocr.confidence,
            )
            for index, row in enumerate(rows, start=1)
            if any(word.text.strip() for word in row)
        ]


def _has_bbox(word: OCRWord) -> bool:
    return None not in (word.left, word.top, word.width, word.height)


def _spatial_rows(words: list[OCRWord]) -> list[list[OCRWord]]:
    tolerance = max(4.0, median(float(word.height or 0) for word in words) * 0.65)
    rows: list[list[OCRWord]] = []
    for word in sorted(words, key=lambda item: (item.top or 0, item.left or 0)):
        if not rows or abs((word.top or 0) - median(float(item.top or 0) for item in rows[-1])) > tolerance:
            rows.append([word])
        else:
            rows[-1].append(word)
    return [sorted(row, key=lambda item: item.left or 0) for row in rows]


def _row_bbox(row: list[OCRWord], *, page_width: float, page_height: float) -> BoundingBox:
    x0 = min(float(word.left or 0) for word in row)
    y0 = min(float(word.top or 0) for word in row)
    x1 = max(float((word.left or 0) + (word.width or 0)) for word in row)
    y1 = max(float((word.top or 0) + (word.height or 0)) for word in row)
    scale_x = page_width / max(x1, page_width, 1.0)
    scale_y = page_height / max(y1, page_height, 1.0)
    return BoundingBox(x0=x0 * scale_x, y0=y0 * scale_y, x1=x1 * scale_x, y1=y1 * scale_y)


def _word_bbox(word: OCRWord) -> BoundingBox:
    return BoundingBox(
        x0=float(word.left or 0),
        y0=float(word.top or 0),
        x1=float((word.left or 0) + (word.width or 0)),
        y1=float((word.top or 0) + (word.height or 0)),
    )


def _table(
    *,
    filename: str,
    page_number: int,
    table,
    page_width: float,
    page_height: float,
    confidence: float | None,
    structure_confidence: float,
) -> DocumentBlock:
    header = table.rows[0]
    lines = ["| " + " | ".join(row) + " |" for row in table.rows]
    text = "\n".join([lines[0], "| " + " | ".join("---" for _ in header) + " |", *lines[1:]])
    raw = table.bbox
    scale_x = page_width / max(raw.x1, page_width, 1.0)
    scale_y = page_height / max(raw.y1, page_height, 1.0)
    return DocumentBlock(
        filename=filename,
        file_type=DocumentFileType.PDF,
        page_number=page_number,
        block_index=1,
        text=text,
        processing_mode=ProcessingMode.PDF_OCR,
        source_element=SourceElementType.PDF_RENDER,
        ocr_confidence=confidence,
        block_type=DocumentBlockType.TABLE,
        bbox=BoundingBox(
            x0=raw.x0 * scale_x,
            y0=raw.y0 * scale_y,
            x1=raw.x1 * scale_x,
            y1=raw.y1 * scale_y,
        ),
        reading_order=1,
        structure_confidence=structure_confidence,
        table_header_rows=table.header_rows,
    )


def _paragraph(
    *,
    filename: str,
    page_number: int,
    index: int,
    text: str,
    bbox: BoundingBox | None,
    confidence: float | None,
) -> DocumentBlock:
    return DocumentBlock(
        filename=filename,
        file_type=DocumentFileType.PDF,
        page_number=page_number,
        block_index=index,
        text=text,
        processing_mode=ProcessingMode.PDF_OCR,
        source_element=SourceElementType.PDF_RENDER,
        ocr_confidence=confidence,
        block_type=DocumentBlockType.PARAGRAPH,
        bbox=bbox,
        reading_order=index,
        structure_confidence=0.75 if bbox is not None else 0.45,
    )
