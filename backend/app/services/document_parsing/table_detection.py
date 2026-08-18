from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import median

from .models import BoundingBox


class TableDetectionMethod(str, Enum):
    PYMUPDF_LINES = "pymupdf_lines"
    PYMUPDF_TEXT = "pymupdf_text"
    SPATIAL_HEURISTIC = "spatial_heuristic"


@dataclass(frozen=True)
class SpatialToken:
    text: str
    bbox: BoundingBox


@dataclass(frozen=True)
class DetectedTable:
    bbox: BoundingBox
    rows: tuple[tuple[str, ...], ...]
    header_rows: int
    method: TableDetectionMethod
    confidence: float
    tokens: tuple[SpatialToken, ...] = ()


@dataclass(frozen=True)
class TableValidationResult:
    accepted: bool
    confidence: float
    reasons: tuple[str, ...]


class TableCandidateValidator:
    def validate(self, candidate: DetectedTable | None) -> TableValidationResult:
        if candidate is None:
            return TableValidationResult(False, 0.0, ("no_candidate",))
        rows = candidate.rows
        columns = max((len(row) for row in rows), default=0)
        total = sum(len(row) for row in rows)
        non_empty = sum(bool(cell.strip()) for row in rows for cell in row)
        reasons: list[str] = []
        if len(rows) < 2:
            reasons.append("insufficient_rows")
        if columns < 2:
            reasons.append("insufficient_columns")
        if not total or non_empty / total < 0.6:
            reasons.append("low_non_empty_ratio")
        if candidate.bbox.x1 <= candidate.bbox.x0 or candidate.bbox.y1 <= candidate.bbox.y0:
            reasons.append("invalid_bbox")
        if candidate.method is TableDetectionMethod.SPATIAL_HEURISTIC:
            reasons.extend(_spatial_rejections(candidate))
        accepted = not reasons
        return TableValidationResult(
            accepted=accepted,
            confidence=round(candidate.confidence if accepted else min(candidate.confidence, 0.49), 3),
            reasons=tuple(reasons) if reasons else ("accepted",),
        )


def spatial_table_from_tokens(tokens: tuple[SpatialToken, ...]) -> DetectedTable | None:
    if len(tokens) < 4:
        return None
    rows = _rows(tokens)
    if not rows:
        return None
    values = tuple(tuple(token.text for token in row) for row in rows)
    boxes = [token.bbox for token in tokens]
    return DetectedTable(
        bbox=BoundingBox(
            x0=min(box.x0 for box in boxes),
            y0=min(box.y0 for box in boxes),
            x1=max(box.x1 for box in boxes),
            y1=max(box.y1 for box in boxes),
        ),
        rows=values,
        header_rows=1,
        method=TableDetectionMethod.SPATIAL_HEURISTIC,
        confidence=0.85,
        tokens=tokens,
    )


def _spatial_rejections(candidate: DetectedTable) -> list[str]:
    rows = _rows(candidate.tokens)
    if len(rows) < 3:
        return ["insufficient_spatial_rows"]
    widths = [token.bbox.x1 - token.bbox.x0 for token in candidate.tokens]
    values = [cell.strip() for row in candidate.rows for cell in row]
    if (
        widths
        and median(widths) > (candidate.bbox.x1 - candidate.bbox.x0) * 0.35
        and median(len(value) for value in values) > 45
    ):
        return ["long_prose_cells"]
    if any(value.startswith(("-", "*", "•")) for value in values):
        return ["aligned_list"]
    labels = [row[0].strip() for row in candidate.rows if row]
    if labels and sum(label.endswith(":") for label in labels) / len(labels) >= 0.6:
        return ["form_like_labels"]
    column_counts = {len(row) for row in candidate.rows}
    if len(column_counts) != 1:
        return ["inconsistent_column_count"]
    for column in range(len(rows[0])):
        positions = [row[column].bbox.x0 for row in rows]
        if max(positions) - min(positions) > max(12.0, (candidate.bbox.x1 - candidate.bbox.x0) * 0.08):
            return ["misaligned_columns"]
    spacing = [
        median(token.bbox.y0 for token in rows[index + 1]) - median(token.bbox.y0 for token in rows[index])
        for index in range(len(rows) - 1)
    ]
    if min(spacing) <= 0 or max(spacing) - min(spacing) > max(10.0, median(spacing) * 0.6):
        return ["inconsistent_row_spacing"]
    return []


def _rows(tokens: tuple[SpatialToken, ...]) -> list[list[SpatialToken]]:
    heights = [token.bbox.y1 - token.bbox.y0 for token in tokens]
    tolerance = max(4.0, median(heights) * 0.75)
    rows: list[list[SpatialToken]] = []
    for token in sorted(tokens, key=lambda item: (item.bbox.y0, item.bbox.x0)):
        if not rows or abs(token.bbox.y0 - median(item.bbox.y0 for item in rows[-1])) > tolerance:
            rows.append([token])
        else:
            rows[-1].append(token)
    return [sorted(row, key=lambda item: item.bbox.x0) for row in rows]
