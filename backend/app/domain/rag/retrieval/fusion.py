from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .domain import (
    CandidateScoreProvenance,
    FusedCandidate,
    RetrievalCandidate,
    RetrievalSource,
)


_SOURCE_ORDER: dict[RetrievalSource, int] = {
    "vector": 0,
    "keyword": 1,
    "metadata": 2,
}


@dataclass(frozen=True, slots=True)
class ReciprocalRankFusion:
    k: int = 60

    def __post_init__(self) -> None:
        if self.k <= 0:
            raise ValueError("RRF k must be positive")

    def fuse(
        self,
        candidates_by_source: Mapping[
            RetrievalSource, Sequence[RetrievalCandidate]
        ],
    ) -> tuple[FusedCandidate, ...]:
        best_occurrences: dict[
            tuple[RetrievalSource, str, str], RetrievalCandidate
        ] = {}
        for candidates in candidates_by_source.values():
            for candidate in candidates:
                key = (candidate.retriever, candidate.query, candidate.chunk_id)
                current = best_occurrences.get(key)
                if current is None or _occurrence_order(candidate) < _occurrence_order(
                    current
                ):
                    best_occurrences[key] = candidate

        grouped: dict[str, list[RetrievalCandidate]] = {}
        for candidate in best_occurrences.values():
            grouped.setdefault(candidate.chunk_id, []).append(candidate)

        unranked: list[FusedCandidate] = []
        for chunk_id, occurrences in grouped.items():
            ordered = sorted(occurrences, key=_provenance_order)
            canonical = min(ordered, key=_canonical_order)
            provenance = tuple(
                CandidateScoreProvenance(
                    retriever=candidate.retriever,
                    query=candidate.query,
                    rank=candidate.rank,
                    raw_score=candidate.raw_score,
                    score_kind=candidate.score_kind,
                    higher_is_better=candidate.higher_is_better,
                    rrf_contribution=1.0 / (self.k + candidate.rank),
                )
                for candidate in ordered
            )
            unranked.append(
                FusedCandidate(
                    chunk_id=chunk_id,
                    document_id=canonical.document_id,
                    index_version_id=canonical.index_version_id,
                    content=canonical.content,
                    citation_label=canonical.citation_label,
                    source_title=canonical.source_title,
                    source_url=canonical.source_url,
                    trusted_level=canonical.trusted_level,
                    metadata=canonical.metadata,
                    rrf_score=sum(item.rrf_contribution for item in provenance),
                    fused_rank=1,
                    provenance=provenance,
                )
            )

        ordered_fused = sorted(
            unranked,
            key=lambda candidate: (-candidate.rrf_score, candidate.chunk_id),
        )
        return tuple(
            candidate.model_copy(update={"fused_rank": rank})
            for rank, candidate in enumerate(ordered_fused, start=1)
        )


def _occurrence_order(candidate: RetrievalCandidate) -> tuple[int, float, str]:
    score = -candidate.raw_score if candidate.higher_is_better else candidate.raw_score
    return candidate.rank, score, candidate.score_kind


def _provenance_order(
    candidate: RetrievalCandidate,
) -> tuple[int, str, int, str, float]:
    return (
        _SOURCE_ORDER[candidate.retriever],
        candidate.query,
        candidate.rank,
        candidate.score_kind,
        candidate.raw_score,
    )


def _canonical_order(candidate: RetrievalCandidate) -> tuple[int, str, int]:
    return _SOURCE_ORDER[candidate.retriever], candidate.query, candidate.rank
