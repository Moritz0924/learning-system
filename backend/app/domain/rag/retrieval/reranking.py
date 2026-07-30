from __future__ import annotations

import re
from dataclasses import dataclass
from time import perf_counter_ns

from .domain import FusedCandidate, RetrievalRequest
from .ports import RerankerTimeoutError


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
        del request
        deadline_ns = _deadline_ns(timeout_ms)
        reranked: list[FusedCandidate] = []
        for rank, candidate in enumerate(candidates, start=1):
            _check_deadline(deadline_ns)
            reranked.append(
                candidate.model_copy(
                    update={
                        "rerank_score": candidate.rrf_score,
                        "reranked_rank": rank,
                    }
                )
            )
        _check_deadline(deadline_ns)
        return tuple(reranked)


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
        deadline_ns = _deadline_ns(timeout_ms)
        query_features = _lexical_features(request.query, deadline_ns=deadline_ns)
        scored: list[tuple[float, FusedCandidate]] = []
        for candidate in candidates:
            _check_deadline(deadline_ns)
            scored.append(
                (
                    _score(
                        candidate,
                        query_features=query_features,
                        lexical_weight=self.lexical_weight,
                        trust_weight=self.trust_weight,
                        deadline_ns=deadline_ns,
                    ),
                    candidate,
                )
            )
        _check_deadline(deadline_ns)
        ordered = sorted(
            scored,
            key=lambda item: (
                -item[0],
                item[1].fused_rank,
                item[1].chunk_id,
            ),
        )
        _check_deadline(deadline_ns)
        reranked: list[FusedCandidate] = []
        for rank, (score, candidate) in enumerate(ordered, start=1):
            _check_deadline(deadline_ns)
            reranked.append(
                candidate.model_copy(
                    update={"rerank_score": score, "reranked_rank": rank}
                )
            )
        _check_deadline(deadline_ns)
        return tuple(reranked)


def _score(
    candidate: FusedCandidate,
    *,
    query_features: frozenset[str],
    lexical_weight: float,
    trust_weight: float,
    deadline_ns: int,
) -> float:
    _check_deadline(deadline_ns)
    content_features = _lexical_features(candidate.content, deadline_ns=deadline_ns)
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


def _tokens(value: str, *, deadline_ns: int) -> frozenset[str]:
    _check_deadline(deadline_ns)
    tokens = frozenset(
        re.findall(r"[\w-]+", value.casefold(), flags=re.UNICODE)
    )
    _check_deadline(deadline_ns)
    return tokens


def _lexical_features(value: str, *, deadline_ns: int) -> frozenset[str]:
    features = _tokens(value, deadline_ns=deadline_ns) | _cjk_ngrams(
        value, deadline_ns=deadline_ns
    )
    _check_deadline(deadline_ns)
    return features


def _cjk_ngrams(value: str, *, deadline_ns: int) -> frozenset[str]:
    grams: set[str] = set()
    for match in _CJK_RUN_PATTERN.finditer(value.casefold()):
        _check_deadline(deadline_ns)
        run = match.group()
        for size in (2, 3):
            for offset in range(len(run) - size + 1):
                if offset % 64 == 0:
                    _check_deadline(deadline_ns)
                grams.add(run[offset : offset + size])
    _check_deadline(deadline_ns)
    return frozenset(grams)


def _deadline_ns(timeout_ms: int) -> int:
    if timeout_ms <= 0:
        raise RerankerTimeoutError("reranker deadline exceeded")
    return perf_counter_ns() + timeout_ms * 1_000_000


def _check_deadline(deadline_ns: int) -> None:
    if perf_counter_ns() >= deadline_ns:
        raise RerankerTimeoutError("reranker deadline exceeded")
