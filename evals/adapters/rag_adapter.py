"""Engine-compatible adapter that captures one full timed retrieval trace."""
from __future__ import annotations

from adaptive_tutor.phase2.schemas import RetrievedChunk
from adaptive_tutor.phase2.telemetry import TimedRetrievalResult


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
    ) -> None:
        if retrieval_limit < 1:
            raise ValueError("retrieval_limit must be positive")
        if not 1 <= generation_context_k <= retrieval_limit:
            raise ValueError("generation_context_k must be between 1 and retrieval_limit")
        self.repository = repository
        self.retrieval_limit = retrieval_limit
        self.generation_context_k = generation_context_k
        self.last_trace: TimedRetrievalResult | None = None

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        user_id: str | None = None,
    ) -> list[RetrievedChunk]:
        del top_k  # Engine's fixed value must not cause repeated or undersized metric retrievals.
        self.last_trace = self.repository.retrieve_timed(
            query,
            top_k=self.retrieval_limit,
            user_id=user_id,
        )
        if self.last_trace.status == "failed":
            raise EvaluationRetrievalError(self.last_trace)
        return self.last_trace.chunks[: self.generation_context_k]
