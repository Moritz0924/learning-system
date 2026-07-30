from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from time import perf_counter_ns
from typing import Literal

from .analysis import QueryAnalyzer
from .domain import (
    FusedCandidate,
    QueryAnalysis,
    QueryRewriteTrace,
    RetrievalCandidate,
    RetrievalRequest,
    RetrievalResult,
    RetrievalSource,
    RetrievalSourceTrace,
    RetrievalTrace,
)
from .fusion import ReciprocalRankFusion
from .ports import (
    KeywordRetriever,
    MetadataRetriever,
    QueryRewritePort,
    RerankerPort,
    RerankerTimeoutError,
    VectorRetriever,
)
from .reranking import HeuristicReranker
from .selection import ContextSelector


@dataclass(slots=True)
class RetrievalOrchestrator:
    vector_retriever: VectorRetriever
    keyword_retriever: KeywordRetriever
    metadata_retriever: MetadataRetriever
    query_rewriter: QueryRewritePort | None = None
    analyzer: QueryAnalyzer = QueryAnalyzer()
    fusion: ReciprocalRankFusion = field(default_factory=ReciprocalRankFusion)
    reranker: RerankerPort = field(default_factory=HeuristicReranker)
    rerank_timeout_ms: int = 100
    context_selector: ContextSelector = field(default_factory=ContextSelector)

    def __post_init__(self) -> None:
        if self.rerank_timeout_ms <= 0:
            raise ValueError("rerank_timeout_ms must be positive")

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        analysis = self.analyzer.analyze(request.query)
        queries, rewrite_trace = self._queries(analysis)
        candidate_lists: dict[RetrievalSource, list[RetrievalCandidate]] = {
            "vector": [],
            "keyword": [],
            "metadata": [],
        }
        attempts: list[RetrievalSourceTrace] = []

        for source, retriever in (
            ("vector", self.vector_retriever),
            ("keyword", self.keyword_retriever),
        ):
            for query in queries:
                candidates, trace = _retrieve_source(
                    source=source,
                    retriever=retriever,
                    request=request,
                    query=query,
                    analysis=analysis,
                )
                candidate_lists[source].extend(candidates)
                attempts.append(trace)

        metadata_candidates, metadata_trace = _retrieve_source(
            source="metadata",
            retriever=self.metadata_retriever,
            request=request,
            query=queries[0],
            analysis=analysis,
        )
        candidate_lists["metadata"].extend(metadata_candidates)
        attempts.append(metadata_trace)

        immutable_lists = {
            source: tuple(candidates) for source, candidates in candidate_lists.items()
        }
        fusion_started = perf_counter_ns()
        fused_candidates = self.fusion.fuse(immutable_lists)
        fusion_elapsed_ms = (perf_counter_ns() - fusion_started) / 1_000_000
        (
            reranked_candidates,
            rerank_status,
            rerank_elapsed_ms,
            fallback_reasons,
        ) = _rerank_with_fallback(
            self.reranker,
            request=request,
            candidates=fused_candidates,
            timeout_ms=self.rerank_timeout_ms,
        )
        selection_started = perf_counter_ns()
        selected_candidates = self.context_selector.select(reranked_candidates)
        selection_elapsed_ms = (perf_counter_ns() - selection_started) / 1_000_000
        selected_char_count = self.context_selector.context_char_count(
            selected_candidates
        )
        has_candidates = any(immutable_lists.values())
        substantive_attempts = tuple(
            attempt
            for attempt in attempts
            if attempt.source != "metadata" or request.filters.has_metadata_constraints
        )
        has_success = any(attempt.status == "succeeded" for attempt in substantive_attempts)
        status = "grounded" if has_candidates else "no_context" if has_success else "failed"
        error_code = None
        if status == "failed":
            error_code = next(
                (attempt.error_code for attempt in attempts if attempt.error_code),
                "retrieval_failed",
            )
        trace = RetrievalTrace(
            original_query=analysis.original_query,
            normalized_query=analysis.normalized_query,
            exact_terms=analysis.exact_terms,
            queries=queries,
            rewrite=rewrite_trace,
            source_attempts=tuple(attempts),
            fused_candidates=fused_candidates,
            reranked_candidates=reranked_candidates,
            selected_candidates=selected_candidates,
            fusion_elapsed_ms=fusion_elapsed_ms,
            rerank_elapsed_ms=rerank_elapsed_ms,
            selection_elapsed_ms=selection_elapsed_ms,
            selected_char_count=selected_char_count,
            rerank_status=rerank_status,
            fallback_reasons=fallback_reasons,
        )
        return RetrievalResult(
            status=status,
            request=request,
            analysis=analysis,
            queries=queries,
            candidates_by_source=immutable_lists,
            fused_candidates=fused_candidates,
            reranked_candidates=reranked_candidates,
            selected_candidates=selected_candidates,
            trace=trace,
            error_code=error_code,
        )

    def _queries(self, analysis: QueryAnalysis) -> tuple[tuple[str, ...], QueryRewriteTrace]:
        if self.query_rewriter is None:
            return (analysis.normalized_query,), QueryRewriteTrace(status="not_configured")
        try:
            rewritten = tuple(
                dict.fromkeys(
                    normalized
                    for value in self.query_rewriter.rewrite(analysis)
                    if (normalized := " ".join(value.split()))
                    and normalized != analysis.normalized_query
                )
            )
        except Exception:
            return (analysis.normalized_query,), QueryRewriteTrace(
                status="failed",
                error_code="query_rewrite_failed",
            )
        return (analysis.normalized_query, *rewritten), QueryRewriteTrace(
            status="succeeded",
            rewritten_queries=rewritten,
        )


def _retrieve_source(
    *,
    source: RetrievalSource,
    retriever: object,
    request: RetrievalRequest,
    query: str,
    analysis: QueryAnalysis,
) -> tuple[tuple[RetrievalCandidate, ...], RetrievalSourceTrace]:
    started = perf_counter_ns()
    try:
        candidates = tuple(retriever.retrieve(request, query=query, analysis=analysis))
    except Exception as exc:
        return (), RetrievalSourceTrace(
            source=source,
            query=query,
            status="failed",
            elapsed_ms=(perf_counter_ns() - started) / 1_000_000,
            error_code=_source_error_code(exc),
        )
    return candidates, RetrievalSourceTrace(
        source=source,
        query=query,
        status="succeeded",
        candidate_ids=tuple(candidate.chunk_id for candidate in candidates),
        elapsed_ms=(perf_counter_ns() - started) / 1_000_000,
    )


def _source_error_code(exc: Exception) -> str:
    if exc.__class__.__name__ == "EmbeddingUnavailable":
        return "embedding_unavailable"
    if any(base.__name__ == "SQLAlchemyError" for base in exc.__class__.__mro__):
        return "retrieval_database_error"
    return "retrieval_source_error"


def _rerank_with_fallback(
    reranker: RerankerPort,
    *,
    request: RetrievalRequest,
    candidates: tuple[FusedCandidate, ...],
    timeout_ms: int,
) -> tuple[
    tuple[FusedCandidate, ...],
    Literal["not_run", "succeeded", "failed", "timed_out"],
    float,
    tuple[str, ...],
]:
    if not candidates:
        return (), "not_run", 0.0, ()
    started = perf_counter_ns()
    try:
        reranked = tuple(
            reranker.rerank(request, candidates, timeout_ms=timeout_ms)
        )
        fused_by_id = {candidate.chunk_id: candidate for candidate in candidates}
        reranked_ids = tuple(candidate.chunk_id for candidate in reranked)
        if (
            len(reranked_ids) != len(fused_by_id)
            or len(set(reranked_ids)) != len(reranked_ids)
            or set(reranked_ids) != set(fused_by_id)
        ):
            raise ValueError("reranker must return each fused candidate exactly once")
        normalized_candidates: list[FusedCandidate] = []
        for rank, candidate in enumerate(reranked, start=1):
            fused = fused_by_id[candidate.chunk_id]
            score = (
                candidate.rerank_score
                if candidate.rerank_score is not None
                else fused.rrf_score
            )
            normalized_score = float(score)
            if not isfinite(normalized_score):
                raise ValueError("reranker scores must be finite numbers")
            normalized_candidates.append(
                fused.model_copy(
                    update={
                        "rerank_score": normalized_score,
                        "reranked_rank": rank,
                    }
                )
            )
        normalized = tuple(normalized_candidates)
    except RerankerTimeoutError:
        return (
            candidates,
            "timed_out",
            (perf_counter_ns() - started) / 1_000_000,
            ("reranker_timeout",),
        )
    except Exception:
        return (
            candidates,
            "failed",
            (perf_counter_ns() - started) / 1_000_000,
            ("reranker_failed",),
        )
    return (
        normalized,
        "succeeded",
        (perf_counter_ns() - started) / 1_000_000,
        (),
    )
