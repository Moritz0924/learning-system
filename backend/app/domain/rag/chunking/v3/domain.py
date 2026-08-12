from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from backend.app.services.document_parsing.models import DocumentBlockType


class BoundaryStrength(str, Enum):
    HARD = "hard"
    STRONG = "strong"
    SOFT = "soft"


@dataclass(frozen=True)
class StructuralUnit:
    unit_id: str
    text: str
    block_type: DocumentBlockType
    page_number: int
    block_index: int
    heading_path: tuple[str, ...]
    bbox: tuple[float, float, float, float] | None
    reading_order: int
    structure_confidence: float | None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StructuralRegion:
    region_id: str
    heading_path: tuple[str, ...]
    units: tuple[StructuralUnit, ...]
    region_type: str
    boundary_before: BoundaryStrength
    boundary_after: BoundaryStrength


@dataclass(frozen=True)
class SemanticUnit:
    unit_id: str
    text: str
    source_unit_ids: tuple[str, ...]
    page_start: int
    page_end: int
    heading_path: tuple[str, ...]
    block_type: DocumentBlockType
    order: int


@dataclass(frozen=True)
class SemanticBoundary:
    left_unit_id: str
    right_unit_id: str
    local_similarity: float
    adjacent_similarity: float
    continuation_score: float
    continuation_reasons: tuple[str, ...]
    boundary_score: float
    adaptive_threshold: float | None
    selected: bool


@dataclass(frozen=True)
class SemanticSegment:
    units: tuple[SemanticUnit, ...]
    boundaries: tuple[SemanticBoundary, ...] = ()


@dataclass(frozen=True)
class ChunkCandidate:
    content: str
    chunk_type: str
    heading_path: tuple[str, ...]
    source_unit_ids: tuple[str, ...]
    page_start: int
    page_end: int
    metadata: Mapping[str, Any] = field(default_factory=dict)


__all__ = [
    "BoundaryStrength", "ChunkCandidate", "SemanticBoundary", "SemanticSegment",
    "SemanticUnit", "StructuralRegion", "StructuralUnit",
]
