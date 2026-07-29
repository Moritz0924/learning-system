from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns

from .analysis import QueryAnalyzer
from .domain import (
    QueryAnalysis,
    QueryRewriteTrace,
    RetrievalCandidate,
    RetrievalRequest,
    RetrievalResult,
    RetrievalSource,
    RetrievalSourceTrace,
    RetrievalTrace,
)
from .ports import KeywordRetriever, MetadataRetriever, QueryRewritePort, VectorRetriever


@dataclass(slots=True)
class RetrievalOrchestrator:
    vector_retriever: VectorRetriever
    keyword_retriever: KeywordRetriever
    metadata_retriever: MetadataRetriever
    query_rewriter: QueryRewritePort | None = None
    analyzer: QueryAnalyzer = QueryAnalyzer()

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
        )
        return RetrievalResult(
            status=status,
            request=request,
            analysis=analysis,
            queries=queries,
            candidates_by_source=immutable_lists,
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
