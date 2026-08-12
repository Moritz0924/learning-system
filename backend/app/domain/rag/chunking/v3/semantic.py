from __future__ import annotations

from math import sqrt
from typing import Sequence

from .config import SemanticChunkPolicy
from .domain import (
    SemanticBoundary,
    SemanticSegmentation,
    SemanticSegment,
    SemanticTrace,
    SemanticUnit,
    StructuralRegion,
)
from .ports import SemanticEncoderPort
from .relations import AdjacentRelationChecker
from .sentence_splitter import SentenceSplitter
from .threshold import AdaptiveThresholdPolicy


class SemanticChunker:
    def __init__(
        self,
        *,
        encoder: SemanticEncoderPort,
        relation_checker: AdjacentRelationChecker | None = None,
        threshold_policy: AdaptiveThresholdPolicy | None = None,
        policy: SemanticChunkPolicy | None = None,
        batch_size: int = 64,
    ) -> None:
        self.encoder = encoder
        self.relation_checker = relation_checker or AdjacentRelationChecker()
        self.policy = policy or SemanticChunkPolicy()
        self.batch_size = max(1, batch_size)
        self.threshold_policy = threshold_policy or AdaptiveThresholdPolicy(
            min_samples=self.policy.min_boundary_samples,
            mad_multiplier=self.policy.mad_multiplier,
        )
        self.sentence_splitter = SentenceSplitter()

    def split(self, region: StructuralRegion) -> list[SemanticSegment]:
        return list(self.split_with_trace(region).segments)

    def split_with_trace(self, region: StructuralRegion) -> SemanticSegmentation:
        if region.region_type in {"code", "table"}:
            return SemanticSegmentation(
                segments=(SemanticSegment(
                    units=tuple(_semantic_unit(unit, index) for index, unit in enumerate(region.units)),
                ),),
                trace=SemanticTrace(region.region_id, ()),
            )
        units = _semantic_units(
            region,
            max_chars=self.policy.max_semantic_unit_chars,
            sentence_splitter=self.sentence_splitter,
        )
        if len(units) > self.policy.max_semantic_units:
            from .errors import HybridChunkingInvariantViolation
            raise HybridChunkingInvariantViolation("maximum semantic units exceeded")
        if len(units) <= 1:
            return SemanticSegmentation(
                segments=(SemanticSegment(units=tuple(units)),),
                trace=SemanticTrace(region.region_id, ()),
            )
        vectors: list[Sequence[float]] = []
        texts = [unit.text for unit in units]
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(self.encoder.embed_batch(texts[start : start + self.batch_size]))
        if len(vectors) != len(units):
            raise ValueError("semantic encoder returned an unexpected vector count")
        boundaries = [_boundary(
            units=units,
            vectors=vectors,
            index=index,
            policy=self.policy,
            relation_checker=self.relation_checker,
        ) for index in range(len(units) - 1)]
        threshold = self.threshold_policy.threshold([boundary.boundary_score for boundary in boundaries])
        selected_boundaries = tuple(
            SemanticBoundary(
                **{
                    **boundary.__dict__,
                    "adaptive_threshold": threshold,
                    "selected": self.threshold_policy.select(boundary.boundary_score, threshold),
                }
            )
            for boundary in boundaries
        )
        trace = SemanticTrace(region.region_id, selected_boundaries)
        selected_indexes = [
            index for index, boundary in enumerate(selected_boundaries) if boundary.selected
        ]
        segments: list[SemanticSegment] = []
        start = 0
        for boundary_index in selected_indexes:
            segments.append(SemanticSegment(
                units=tuple(units[start : boundary_index + 1]),
                boundary_before=selected_boundaries[start - 1] if start else None,
                boundary_after=selected_boundaries[boundary_index],
            ))
            start = boundary_index + 1
        segments.append(SemanticSegment(
            units=tuple(units[start:]),
            boundary_before=selected_boundaries[start - 1] if start else None,
        ))
        return SemanticSegmentation(segments=tuple(segments), trace=trace)


def _boundary(
    *,
    units: Sequence[SemanticUnit],
    vectors: Sequence[Sequence[float]],
    index: int,
    policy: SemanticChunkPolicy,
    relation_checker: AdjacentRelationChecker,
) -> SemanticBoundary:
    left_start = max(0, index - policy.window_size + 1)
    right_end = min(len(units), index + 1 + policy.window_size)
    local = _cosine(_mean(vectors[left_start : index + 1]), _mean(vectors[index + 1 : right_end]))
    adjacent = _cosine(vectors[index], vectors[index + 1])
    relation = relation_checker.check(units[index], units[index + 1])
    score = max(0.0, min(1.0,
        policy.local_window_weight * ((1 - local) / 2)
        + policy.adjacent_weight * ((1 - adjacent) / 2)
        - policy.relation_penalty_weight * relation.continuation_score
    ))
    return SemanticBoundary(
        left_unit_id=units[index].unit_id,
        right_unit_id=units[index + 1].unit_id,
        local_similarity=local,
        adjacent_similarity=adjacent,
        continuation_score=relation.continuation_score,
        continuation_reasons=relation.reasons,
        boundary_score=score,
        adaptive_threshold=None,
        selected=False,
    )


def _semantic_units(
    region: StructuralRegion,
    *,
    max_chars: int,
    sentence_splitter: SentenceSplitter,
) -> list[SemanticUnit]:
    result: list[SemanticUnit] = []
    for unit in region.units:
        pieces = sentence_splitter.split(unit.text)
        pieces = [
            fragment
            for piece in (pieces or [unit.text])
            for fragment in _split_oversized_unit(piece, max_chars=max_chars)
        ]
        for sentence_index, piece in enumerate(pieces):
            result.append(_semantic_unit(
                unit,
                len(result),
                text=piece,
                suffix=sentence_index if len(pieces) > 1 else None,
            ))
    return result


def _semantic_unit(unit, index: int, *, text: str | None = None, suffix: int | None = None) -> SemanticUnit:
    return SemanticUnit(
        unit_id=f"{unit.unit_id}:s{suffix}" if suffix is not None else unit.unit_id,
        text=text if text is not None else unit.text,
        source_unit_ids=(unit.unit_id,),
        page_start=unit.page_number,
        page_end=unit.page_number,
        heading_path=unit.heading_path,
        block_type=unit.block_type,
        order=index,
    )


def _split_oversized_unit(text: str, *, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]


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
