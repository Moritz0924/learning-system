from __future__ import annotations


def _token(text: str, x: float, y: float, width: float = 40, height: float = 12):
    from backend.app.services.document_parsing.models import BoundingBox
    from backend.app.services.document_parsing.table_detection import SpatialToken

    return SpatialToken(text=text, bbox=BoundingBox(x0=x, y0=y, x1=x + width, y1=y + height))


def test_spatial_table_validator_accepts_aligned_dense_three_row_table() -> None:
    from backend.app.services.document_parsing.table_detection import (
        DetectedTable,
        TableCandidateValidator,
        TableDetectionMethod,
        spatial_table_from_tokens,
    )

    tokens = tuple(
        _token(text, x, y)
        for y, row in ((10, ("Metric", "Score")), (30, ("Recall", "0.91")), (50, ("MRR", "0.88")))
        for x, text in ((10, row[0]), (80, row[1]))
    )
    candidate = spatial_table_from_tokens(tokens)

    assert candidate is not None
    result = TableCandidateValidator().validate(candidate)

    assert result.accepted is True
    assert result.confidence >= 0.8
    assert candidate.method is TableDetectionMethod.SPATIAL_HEURISTIC


def test_spatial_table_validator_rejects_two_column_academic_layout_and_aligned_bullets() -> None:
    from backend.app.services.document_parsing.table_detection import TableCandidateValidator, spatial_table_from_tokens

    academic = tuple(
        _token(text, x, y, width=200)
        for y, row in (
            (10, ("Long academic prose in the left column continues across the page.", "Long academic prose in the right column continues across the page.")),
            (35, ("The next left paragraph remains ordinary narrative text.", "The next right paragraph remains ordinary narrative text.")),
            (60, ("A third row only reflects reading order, not a table.", "A third counterpart is still narrative text.")),
        )
        for x, text in ((10, row[0]), (260, row[1]))
    )
    bullets = tuple(
        _token(text, x, y)
        for y, row in ((10, ("- first", "detail")), (30, ("- second", "detail")), (50, ("- third", "detail")))
        for x, text in ((10, row[0]), (100, row[1]))
    )

    validator = TableCandidateValidator()
    academic_result = validator.validate(spatial_table_from_tokens(academic))
    bullet_result = validator.validate(spatial_table_from_tokens(bullets))

    assert academic_result.accepted is False
    assert "long_prose_cells" in academic_result.reasons
    assert bullet_result.accepted is False
    assert "aligned_list" in bullet_result.reasons


def test_spatial_table_validator_rejects_form_and_low_confidence_two_row_regions() -> None:
    from backend.app.services.document_parsing.table_detection import TableCandidateValidator, spatial_table_from_tokens

    form = tuple(
        _token(text, x, y)
        for y, row in ((10, ("Name:", "Ada")), (30, ("Email:", "ada@example.test")), (50, ("Role:", "Learner")))
        for x, text in ((10, row[0]), (100, row[1]))
    )
    two_rows = tuple(
        _token(text, x, y)
        for y, row in ((10, ("Header", "Value")), (30, ("Field", "Entry")))
        for x, text in ((10, row[0]), (90, row[1]))
    )

    validator = TableCandidateValidator()
    form_result = validator.validate(spatial_table_from_tokens(form))
    two_rows_result = validator.validate(spatial_table_from_tokens(two_rows))

    assert form_result.accepted is False
    assert "form_like_labels" in form_result.reasons
    assert two_rows_result.accepted is False
    assert "insufficient_spatial_rows" in two_rows_result.reasons


def test_pymupdf_table_candidate_still_requires_basic_shape_validation() -> None:
    from backend.app.services.document_parsing.models import BoundingBox
    from backend.app.services.document_parsing.table_detection import (
        DetectedTable,
        TableCandidateValidator,
        TableDetectionMethod,
    )

    candidate = DetectedTable(
        bbox=BoundingBox(x0=0, y0=0, x1=100, y1=40),
        rows=(("Header", ""), ("", "")),
        header_rows=1,
        method=TableDetectionMethod.PYMUPDF_LINES,
        confidence=0.95,
    )

    result = TableCandidateValidator().validate(candidate)

    assert result.accepted is False
    assert "low_non_empty_ratio" in result.reasons


def test_structured_ocr_builder_uses_shared_spatial_validator_for_real_tables() -> None:
    from backend.app.services.document_parsing.models import DocumentBlockType, OCRResult, OCRWord
    from backend.app.services.document_parsing.structured_ocr import StructuredOCRBlockBuilder

    words = [
        OCRWord(text=text, confidence=0.98, left=x, top=y, width=40, height=12)
        for y, row in ((10, ("Metric", "Score")), (30, ("Recall", "0.91")), (50, ("MRR", "0.88")))
        for x, text in ((10, row[0]), (80, row[1]))
    ]
    result = OCRResult(
        text=" ".join(word.text for word in words),
        confidence=0.98,
        word_count=len(words),
        text_char_count=sum(len(word.text) for word in words),
        words=words,
    )

    blocks = StructuredOCRBlockBuilder().build(
        ocr=result,
        filename="table-scan.pdf",
        page_number=1,
        page_width=600,
        page_height=800,
    )

    assert len(blocks) == 1
    assert blocks[0].block_type is DocumentBlockType.TABLE
    assert blocks[0].table_header_rows == 1
    assert "| Metric | Score |" in blocks[0].text
