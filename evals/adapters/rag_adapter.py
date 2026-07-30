"""Engine-compatible adapter that captures one full timed retrieval trace."""
from __future__ import annotations

from time import perf_counter_ns

from adaptive_tutor.phase2.schemas import RetrievedChunk
from adaptive_tutor.phase2.telemetry import TimedRetrievalResult
from backend.app.domain.rag.retrieval import RetrievalRequest


class EvaluationRetrievalError(RuntimeError):
    def __init__(self, trace: TimedRetrievalResult) -> None:
        super().__init__(trace.error_code or "evaluation retrieval failed")
        self.trace = trace


class EvaluationRagAdapter:
    def __init__(
        self,
        repository: object,
        *,
        retrieval_limit: int,
        generation_context_k: int,
        index_schema: str = "legacy-v1",
    ) -> None:
        if retrieval_limit < 1:
            raise ValueError("retrieval_limit must be positive")
        if not 1 <= generation_context_k <= retrieval_limit:
            raise ValueError("generation_context_k must be between 1 and retrieval_limit")
        self.repository = repository
        self.retrieval_limit = retrieval_limit
        self.generation_context_k = generation_context_k
        if index_schema not in {"legacy-v1", "v2"}:
            raise ValueError(f"unsupported evaluation index schema: {index_schema}")
        self.index_schema = index_schema
        self.last_trace: TimedRetrievalResult | None = None
        self.last_result: object | None = None

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        user_id: str | None = None,
    ) -> list[RetrievedChunk]:
        del top_k  # Engine's fixed value must not cause repeated or undersized metric retrievals.
        if self.index_schema == "v2":
            self.last_trace = self._retrieve_v2(query, user_id=user_id)
        else:
            self.last_result = None
            self.last_trace = self.repository.retrieve_timed(
                query,
                top_k=self.retrieval_limit,
                user_id=user_id,
            )
        if self.last_trace.status == "failed":
            raise EvaluationRetrievalError(self.last_trace)
        return self.last_trace.chunks[: self.generation_context_k]

    def _retrieve_v2(
        self,
        query: str,
        *,
        user_id: str | None,
    ) -> TimedRetrievalResult:
        started = perf_counter_ns()
        result = self.repository.retrieve_v2(
            RetrievalRequest(
                query=query,
                top_k=self.retrieval_limit,
                user_id=user_id,
            )
        )
        self.last_result = result
        chunks = [
            RetrievedChunk(
                chunk_id=candidate.chunk_id,
                document_id=candidate.document_id,
                content=candidate.content,
                citation_label=candidate.citation_label,
                source_title=candidate.source_title,
                source_url=candidate.source_url,
                trusted_level=candidate.trusted_level,
                metadata=dict(candidate.metadata),
            )
            for candidate in result.selected_candidates
        ]
        postprocess_ms = (
            result.trace.fusion_elapsed_ms
            + result.trace.rerank_elapsed_ms
            + result.trace.selection_elapsed_ms
        )
        return TimedRetrievalResult(
            chunks=chunks,
            scores=[],
            embedding_latency_ms=None,
            vector_search_latency_ms=None,
            postprocess_latency_ms=postprocess_ms,
            total_latency_ms=(perf_counter_ns() - started) / 1_000_000.0,
            backend="hybrid_v2",
            top_k=self.retrieval_limit,
            status=result.status,
            error_code=result.error_code,
        )
