from __future__ import annotations

from dataclasses import dataclass

from adaptive_tutor.phase2.schemas import RetrievedChunk

from backend.app.domain.rag.retrieval import (
    RetrievalOrchestrator,
    RetrievalRequest,
    RetrievalResult,
)


@dataclass(frozen=True, slots=True)
class LegacyRetrievalOutcome:
    chunks: tuple[RetrievedChunk, ...]
    status: str
    error_code: str | None
    result: RetrievalResult


@dataclass(slots=True)
class LegacyRetrievalCompatibilityAdapter:
    orchestrator: RetrievalOrchestrator

    def retrieve(self, request: RetrievalRequest) -> LegacyRetrievalOutcome:
        result = self.orchestrator.retrieve(request)
        original_vector_attempt = next(
            (
                attempt
                for attempt in result.trace.source_attempts
                if attempt.source == "vector" and attempt.query == result.queries[0]
            ),
            None,
        )
        if original_vector_attempt is None or original_vector_attempt.status == "failed":
            return LegacyRetrievalOutcome(
                chunks=(),
                status="failed",
                error_code=(
                    original_vector_attempt.error_code
                    if original_vector_attempt is not None
                    else "retrieval_failed"
                ),
                result=result,
            )
        candidates = result.selected_candidates[: request.top_k]
        chunks = tuple(
            RetrievedChunk(
                chunk_id=candidate.chunk_id,
                document_id=candidate.document_id,
                content=candidate.content,
                citation_label=candidate.citation_label,
                source_title=candidate.source_title,
                source_url=candidate.source_url,
                trusted_level=candidate.trusted_level,
                metadata=candidate.model_dump(include={"metadata"})["metadata"],
            )
            for candidate in candidates
        )
        return LegacyRetrievalOutcome(
            chunks=chunks,
            status="grounded" if chunks else "no_context",
            error_code=None,
            result=result,
        )
