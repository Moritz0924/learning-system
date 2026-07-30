from __future__ import annotations

import re
from dataclasses import dataclass

from .domain import FusedCandidate, RetrievalRequest


_CJK_RUN_PATTERN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]+"
)


@dataclass(frozen=True, slots=True)
class NoOpReranker:
    def rerank(
        self,
        request: RetrievalRequest,
        candidates: tuple[FusedCandidate, ...],
        *,
        timeout_ms: int,
    ) -> tuple[FusedCandidate, ...]:
        del request, timeout_ms
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
        *,
        timeout_ms: int,
    ) -> tuple[FusedCandidate, ...]:
        del timeout_ms
        query_features = _lexical_features(request.query)
        scored = [
            (
                _score(
                    candidate,
                    query_features=query_features,
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
    query_features: frozenset[str],
    lexical_weight: float,
    trust_weight: float,
) -> float:
    content_features = _lexical_features(candidate.content)
    lexical_overlap = (
        len(query_features & content_features) / len(query_features)
        if query_features
        else 0.0
    )
    return (
        lexical_weight * lexical_overlap
        + trust_weight * candidate.trusted_level
        + candidate.rrf_score
    )


def _tokens(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"[\w-]+", value.casefold(), flags=re.UNICODE))


def _lexical_features(value: str) -> frozenset[str]:
    return _tokens(value) | _cjk_ngrams(value)


def _cjk_ngrams(value: str) -> frozenset[str]:
    grams: set[str] = set()
    for match in _CJK_RUN_PATTERN.finditer(value.casefold()):
        run = match.group()
        for size in (2, 3):
            grams.update(
                run[offset : offset + size]
                for offset in range(len(run) - size + 1)
            )
    return frozenset(grams)
