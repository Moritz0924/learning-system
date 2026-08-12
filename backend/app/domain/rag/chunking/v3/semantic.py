from __future__ import annotations

from math import sqrt
from typing import Sequence

from backend.app.services.document_parsing.models import DocumentBlockType

from .config import SemanticChunkPolicy
from .domain import SemanticBoundary, SemanticSegment, SemanticUnit, StructuralRegion
from .ports import SemanticEncoderPort
from .relations import AdjacentRelationChecker
from .threshold import AdaptiveThresholdPolicy


class SemanticChunker:
    def __init__(
        self,
        *,
        encoder: SemanticEncoderPort,
        relation_checker: AdjacentRelationChecker | None = None,
        threshold_policy: AdaptiveThresholdPolicy | None = None,
        policy: SemanticChunkPolicy | None = None,
    ) -> None:
        self.encoder = encoder
        self.relation_checker = relation_checker or AdjacentRelationChecker()
        self.policy = policy or SemanticChunkPolicy()
        self.threshold_policy = threshold_policy or AdaptiveThresholdPolicy(
            min_samples=self.policy.min_boundary_samples,
            mad_multiplier=self.policy.mad_multiplier,
        )

    def split(self, region: StructuralRegion) -> list[SemanticSegment]:
        if region.region_type in {"code", "table"}:
            return [SemanticSegment(units=tuple(_semantic_unit(unit, index) for index, unit in enumerate(region.units)))]
        units = _semantic_units(region)
        if len(units) > self.policy.max_semantic_units:
            raise ValueError("maximum semantic units exceeded")
        if len(units) <= 1:
            return [SemanticSegment(units=tuple(units))]
        vectors = self.encoder.embed_batch([unit.text for unit in units])
        if len(vectors) != len(units):
            raise ValueError("semantic encoder returned an unexpected vector count")
        boundaries: list[SemanticBoundary] = []
        for index in range(len(units) - 1):
            left_start = max(0, index - self.policy.window_size + 1)
            right_end = min(len(units), index + 1 + self.policy.window_size)
            local = _cosine(_mean(vectors[left_start : index + 1]), _mean(vectors[index + 1 : right_end]))
            adjacent = _cosine(vectors[index], vectors[index + 1])
            relation = self.relation_checker.check(units[index], units[index + 1])
            score = max(0.0, min(1.0,
                self.policy.local_window_weight * ((1 - local) / 2)
                + self.policy.adjacent_weight * ((1 - adjacent) / 2)
                - self.policy.relation_penalty_weight * relation.continuation_score
            ))
            boundaries.append(SemanticBoundary(
                left_unit_id=units[index].unit_id,
                right_unit_id=units[index + 1].unit_id,
                local_similarity=local,
                adjacent_similarity=adjacent,
                continuation_score=relation.continuation_score,
                continuation_reasons=relation.reasons,
                boundary_score=score,
                adaptive_threshold=None,
                selected=False,
            ))
        threshold = self.threshold_policy.threshold([boundary.boundary_score for boundary in boundaries])
        boundaries = [
            boundary.__class__(**{
                **boundary.__dict__,
                "adaptive_threshold": threshold,
                "selected": self.threshold_policy.select(boundary.boundary_score, threshold),
            })
            for boundary in boundaries
        ]
        selected = {index for index, boundary in enumerate(boundaries) if boundary.selected}
        segments: list[SemanticSegment] = []
        start = 0
        for boundary_index in sorted(selected):
            segments.append(SemanticSegment(units=tuple(units[start : boundary_index + 1]), boundaries=tuple(boundaries)))
            start = boundary_index + 1
        segments.append(SemanticSegment(units=tuple(units[start:]), boundaries=tuple(boundaries)))
        return segments


def _semantic_units(region: StructuralRegion) -> list[SemanticUnit]:
    return [_semantic_unit(unit, index) for index, unit in enumerate(region.units)]


def _semantic_unit(unit, index: int) -> SemanticUnit:
    return SemanticUnit(
        unit_id=unit.unit_id,
        text=unit.text,
        source_unit_ids=(unit.unit_id,),
        page_start=unit.page_number,
        page_end=unit.page_number,
        heading_path=unit.heading_path,
        block_type=unit.block_type,
        order=index,
    )


def _mean(vectors: Sequence[Sequence[float]]) -> list[float]:
    if not vectors:
        return []
    width = len(vectors[0])
    return [sum(vector[index] for vector in vectors) / len(vectors) for index in range(width)]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        return 0.0
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / left_norm / right_norm

