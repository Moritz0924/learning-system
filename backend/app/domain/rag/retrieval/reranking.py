from __future__ import annotations

import re
from dataclasses import dataclass

from .domain import FusedCandidate, RetrievalRequest


@dataclass(frozen=True, slots=True)
class NoOpReranker:
    def rerank(
        self,
        request: RetrievalRequest,
        candidates: tuple[FusedCandidate, ...],
    ) -> tuple[FusedCandidate, ...]:
        del request
        return tuple(
            candidate.model_copy(
                update={
                    "rerank_score": candidate.rrf_score,
                    "reranked_rank": rank,
                }
            )
            for rank, candidate in enumerate(candidates, start=1)
        )


@dataclass(frozen=True, slots=True)
class HeuristicReranker:
    """A deterministic, dependency-free local reranker."""

    lexical_weight: float = 100.0
    trust_weight: float = 0.01

    def rerank(
        self,
        request: RetrievalRequest,
        candidates: tuple[FusedCandidate, ...],
    ) -> tuple[FusedCandidate, ...]:
        query_tokens = _tokens(request.query)
        scored = [
            (
                _score(
                    candidate,
                    query_tokens=query_tokens,
                    lexical_weight=self.lexical_weight,
                    trust_weight=self.trust_weight,
                ),
                candidate,
            )
            for candidate in candidates
        ]
        ordered = sorted(
            scored,
            key=lambda item: (
                -item[0],
                item[1].fused_rank,
                item[1].chunk_id,
            ),
        )
        return tuple(
            candidate.model_copy(
                update={"rerank_score": score, "reranked_rank": rank}
            )
            for rank, (score, candidate) in enumerate(ordered, start=1)
        )


def _score(
    candidate: FusedCandidate,
    *,
    query_tokens: frozenset[str],
    lexical_weight: float,
    trust_weight: float,
) -> float:
    content_tokens = _tokens(candidate.content)
    lexical_overlap = (
        len(query_tokens & content_tokens) / len(query_tokens) if query_tokens else 0.0
    )
    return (
        lexical_weight * lexical_overlap
        + trust_weight * candidate.trusted_level
        + candidate.rrf_score
    )


def _tokens(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"[\w-]+", value.casefold(), flags=re.UNICODE))
