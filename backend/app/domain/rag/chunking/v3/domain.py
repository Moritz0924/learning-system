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
    boundary_before: SemanticBoundary | None = None
    boundary_after: SemanticBoundary | None = None

    @property
    def boundaries(self) -> tuple[SemanticBoundary, ...]:
        """Local compatibility view; never the complete region trace."""
        return (self.boundary_after,) if self.boundary_after is not None else ()


@dataclass(frozen=True)
class SemanticTrace:
    region_id: str
    boundaries: tuple[SemanticBoundary, ...]


@dataclass(frozen=True)
class SemanticSegmentation:
    segments: tuple[SemanticSegment, ...]
    trace: SemanticTrace


@dataclass(frozen=True)
class ChunkCandidate:
    content: str
    chunk_type: str
    heading_path: tuple[str, ...]
    source_unit_ids: tuple[str, ...]
    page_start: int
    page_end: int
    metadata: Mapping[str, Any] = field(default_factory=dict)
    boundaries: tuple[SemanticBoundary, ...] = ()


@dataclass(frozen=True)
class HybridChunkingDiagnostics:
    semantic_regions: int = 0
    adaptive_threshold_regions: int = 0
    candidate_boundaries: int = 0
    selected_boundaries: int = 0
    relation_adjusted_boundaries: int = 0
    tiny_merges: int = 0
    hard_fallbacks: int = 0


@dataclass(frozen=True)
class HybridChunkingResult:
    chunks: tuple[ChunkCandidate, ...]
    diagnostics: HybridChunkingDiagnostics


__all__ = [
    "BoundaryStrength", "ChunkCandidate", "HybridChunkingDiagnostics", "HybridChunkingResult",
    "SemanticBoundary", "SemanticSegmentation", "SemanticSegment", "SemanticTrace", "SemanticUnit",
    "StructuralRegion", "StructuralUnit",
]
