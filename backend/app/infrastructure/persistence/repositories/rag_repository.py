from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from time import perf_counter_ns

from sqlalchemy import and_, exists, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from adaptive_tutor.phase2.schemas import RetrievedChunk
from adaptive_tutor.phase2.telemetry import RetrievalScore, TimedRetrievalResult
from backend.app.application.retrieval_service import LegacyRetrievalCompatibilityAdapter
from backend.app.core.runtime_config import runtime_mode
from backend.app.domain.rag.retrieval import (
    QueryRewritePort,
    RetrievalOrchestrator,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTrace,
)
from backend.app.models import Document, DocumentChunk, DocumentIndexVersion
from backend.app.services.embeddings import EmbeddingUnavailable, pgvector_storage_values
from backend.app.infrastructure.persistence.repositories.rag_retrievers import (
    SQLAlchemyKeywordRetriever,
    SQLAlchemyMetadataRetriever,
    SQLAlchemyVectorRetriever,
)


@dataclass
class SQLAlchemyRagRepository:
    session: Session
    embedding_client: object
    allowed_document_ids: set[str] | None = None
    query_rewriter: QueryRewritePort | None = None
    last_retrieval_status: str = "no_context"
    degraded_reason: str | None = None
    last_retrieval_trace: RetrievalTrace | None = None
    last_retrieval_result: RetrievalResult | None = None

    def retrieve(self, query: str, *, top_k: int = 5, user_id: str | None = None) -> list[RetrievedChunk]:
        outcome = self._compatibility_adapter().retrieve(
            RetrievalRequest(query=query, top_k=top_k, user_id=user_id)
        )
        self.last_retrieval_result = outcome.result
        self.last_retrieval_trace = outcome.result.trace
        self.last_retrieval_status = outcome.status
        self.degraded_reason = outcome.error_code
        return list(outcome.chunks)

    def retrieve_v2(self, request: RetrievalRequest) -> RetrievalResult:
        result = self._orchestrator().retrieve(request)
        self.last_retrieval_result = result
        self.last_retrieval_trace = result.trace
        return result

    def _orchestrator(self) -> RetrievalOrchestrator:
        return RetrievalOrchestrator(
            vector_retriever=SQLAlchemyVectorRetriever(
                self.session,
                self.embedding_client,
                allowed_document_ids=self.allowed_document_ids,
            ),
            keyword_retriever=SQLAlchemyKeywordRetriever(
                self.session,
                allowed_document_ids=self.allowed_document_ids,
            ),
            metadata_retriever=SQLAlchemyMetadataRetriever(
                self.session,
                allowed_document_ids=self.allowed_document_ids,
            ),
            query_rewriter=self.query_rewriter,
        )

    def _compatibility_adapter(self) -> LegacyRetrievalCompatibilityAdapter:
        return LegacyRetrievalCompatibilityAdapter(self._orchestrator())

    def retrieve_timed(
        self,
        query: str,
        *,
        top_k: int = 5,
        user_id: str | None = None,
    ) -> TimedRetrievalResult:
        return self._retrieve_internal(
            query,
            top_k=top_k,
            user_id=user_id,
            collect_timing=True,
        )

    def _retrieve_internal(
        self,
        query: str,
        *,
        top_k: int,
        user_id: str | None,
        collect_timing: bool,
    ) -> TimedRetrievalResult:
        total_started = perf_counter_ns()
        backend = "pgvector" if self._uses_pgvector() else "local_json_embedding"
        try:
            with self.session.begin_nested():
                if backend == "pgvector":
                    chunks, scores, embedding_ms, search_ms, postprocess_ms = self._retrieve_with_pgvector(
                        query,
                        top_k=top_k,
                        user_id=user_id,
                        collect_timing=collect_timing,
                    )
                else:
                    chunks, scores, embedding_ms, search_ms, postprocess_ms = self._retrieve_with_local_embeddings(
                        query,
                        top_k=top_k,
                        user_id=user_id,
                        collect_timing=collect_timing,
                    )
        except EmbeddingUnavailable:
            self.last_retrieval_status = "failed"
            self.degraded_reason = "embedding_unavailable"
            return TimedRetrievalResult(
                chunks=[],
                scores=[],
                embedding_latency_ms=None,
                vector_search_latency_ms=None,
                postprocess_latency_ms=0.0,
                total_latency_ms=_elapsed_ms(total_started, collect_timing),
                backend=backend,
                top_k=top_k,
                status="failed",
                error_code="embedding_unavailable",
            )
        except SQLAlchemyError:
            self.last_retrieval_status = "failed"
            self.degraded_reason = "retrieval_database_error"
            return TimedRetrievalResult(
                chunks=[],
                scores=[],
                embedding_latency_ms=None,
                vector_search_latency_ms=None,
                postprocess_latency_ms=0.0,
                total_latency_ms=_elapsed_ms(total_started, collect_timing),
                backend=backend,
                top_k=top_k,
                status="failed",
                error_code="retrieval_database_error",
            )
        self.last_retrieval_status = "grounded" if chunks else "no_context"
        self.degraded_reason = None
        return TimedRetrievalResult(
            chunks=chunks,
            scores=scores,
            embedding_latency_ms=embedding_ms,
            vector_search_latency_ms=search_ms,
            postprocess_latency_ms=postprocess_ms,
            total_latency_ms=_elapsed_ms(total_started, collect_timing),
            backend=backend,
            top_k=top_k,
            status=self.last_retrieval_status,
            error_code=None,
        )

    def _retrieve_with_local_embeddings(
        self,
        query: str,
        *,
        top_k: int,
        user_id: str | None,
        collect_timing: bool,
    ) -> tuple[list[RetrievedChunk], list[RetrievalScore], float | None, float | None, float]:
        fetch_started = perf_counter_ns()
        visibility_filter = (
            or_(Document.corpus_type == "curated", Document.owner_user_id == user_id)
            if user_id
            else Document.corpus_type == "curated"
        )
        active_index = aliased(DocumentIndexVersion, name="active_index")
        any_index = aliased(DocumentIndexVersion, name="any_index")
        active_or_legacy = or_(
            active_index.status == "active",
            and_(
                DocumentChunk.index_version_id.is_(None),
                ~exists(
                    select(any_index.id).where(
                        any_index.document_id == DocumentChunk.document_id
                    )
                ),
            ),
        )
        statement = (
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .outerjoin(
                active_index,
                and_(
                    active_index.id == DocumentChunk.index_version_id,
                    active_index.document_id == DocumentChunk.document_id,
                ),
            )
            .where(Document.parse_status == "success")
            .where(visibility_filter)
            .where(active_or_legacy)
        )
        if self.allowed_document_ids is not None:
            statement = statement.where(Document.id.in_(self.allowed_document_ids))
        rows = self.session.execute(statement).all()
        fetch_ms = _elapsed_ms(fetch_started, collect_timing)
        if not rows:
            return [], [], None, fetch_ms, 0.0
        embedding_started = perf_counter_ns()
        query_embedding = self.embedding_client.embed(query)
        embedding_ms = _elapsed_ms(embedding_started, collect_timing)
        ranking_started = perf_counter_ns()
        ranked = sorted(
            (
                (_cosine_similarity(query_embedding, chunk.embedding or []), chunk, document)
                for chunk, document in rows
            ),
            key=lambda item: item[0],
            reverse=True,
        )[:top_k]
        search_ms = fetch_ms + _elapsed_ms(ranking_started, collect_timing)
        postprocess_started = perf_counter_ns()
        chunks = [
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=document.id,
                content=chunk.content,
                citation_label=chunk.citation_label,
                source_title=document.filename,
                source_url=document.source_url,
                trusted_level=document.trusted_level,
                metadata={
                    **(chunk.metadata_json or {}),
                    "untrusted_input": document.corpus_type != "curated",
                    "corpus_type": document.corpus_type,
                },
            )
            for _, chunk, document in ranked
        ]
        scores = [
            RetrievalScore(raw_value=score, score_kind="cosine_similarity", higher_is_better=True)
            for score, _, _ in ranked
        ]
        return chunks, scores, embedding_ms, search_ms, _elapsed_ms(postprocess_started, collect_timing)

    def _uses_pgvector(self) -> bool:
        bind = self.session.get_bind()
        return bool(
            bind
            and bind.dialect.name == "postgresql"
            and runtime_mode("RAG_RETRIEVAL_BACKEND", default="pgvector") == "pgvector"
        )

    def _retrieve_with_pgvector(
        self,
        query: str,
        *,
        top_k: int,
        user_id: str | None,
        collect_timing: bool,
    ) -> tuple[list[RetrievedChunk], list[RetrievalScore], float | None, float | None, float]:
        embedding_started = perf_counter_ns()
        query_vector = _vector_literal(self.embedding_client.embed(query))
        embedding_ms = _elapsed_ms(embedding_started, collect_timing)
        search_started = perf_counter_ns()
        rows = list(self.session.execute(
            build_pgvector_retrieval_statement(
                include_owner=user_id is not None,
                restrict_documents=self.allowed_document_ids is not None,
            ),
            {
                "query_vector": query_vector,
                "top_k": top_k,
                "user_id": user_id,
                "allowed_document_ids": sorted(self.allowed_document_ids or ()),
            },
        ).mappings())
        search_ms = _elapsed_ms(search_started, collect_timing)
        postprocess_started = perf_counter_ns()
        chunks = [
            RetrievedChunk(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                content=row["content"],
                citation_label=row["citation_label"],
                source_title=row["source_title"],
                source_url=row["source_url"],
                trusted_level=row["trusted_level"],
                metadata={
                    **(row["metadata_json"] or {}),
                    "untrusted_input": row["corpus_type"] != "curated",
                    "corpus_type": row["corpus_type"],
                },
            )
            for row in rows
        ]
        scores = [
            RetrievalScore(
                raw_value=float(row["distance"]),
                score_kind="cosine_distance",
                higher_is_better=False,
            )
            for row in rows
        ]
        return chunks, scores, embedding_ms, search_ms, _elapsed_ms(postprocess_started, collect_timing)


def build_pgvector_retrieval_statement(
    *,
    include_owner: bool,
    restrict_documents: bool,
):
    owner_clause = "OR documents.owner_user_id = :user_id" if include_owner else ""
    document_scope_clause = (
        "AND documents.id = ANY(CAST(:allowed_document_ids AS text[]))"
        if restrict_documents
        else ""
    )
    return text(
        f"""
        SELECT
            document_chunks.id AS chunk_id,
            document_chunks.document_id AS document_id,
            document_chunks.content AS content,
            document_chunks.citation_label AS citation_label,
            document_chunks.metadata AS metadata_json,
            documents.filename AS source_title,
            documents.source_url AS source_url,
            documents.trusted_level AS trusted_level,
            documents.corpus_type AS corpus_type,
            document_chunks.embedding_vector <=> CAST(:query_vector AS halfvec) AS distance
        FROM document_chunks
        JOIN documents ON documents.id = document_chunks.document_id
        LEFT JOIN document_index_versions AS index_version
          ON index_version.id = document_chunks.index_version_id
         AND index_version.document_id = document_chunks.document_id
        WHERE documents.parse_status = 'success'
          AND document_chunks.embedding_vector IS NOT NULL
          AND (
                index_version.status = 'active'
                OR (
                    document_chunks.index_version_id IS NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM document_index_versions AS any_index
                        WHERE any_index.document_id = document_chunks.document_id
                    )
                )
              )
          AND (documents.corpus_type = 'curated' {owner_clause})
          {document_scope_clause}
        ORDER BY document_chunks.embedding_vector <=> CAST(:query_vector AS halfvec)
        LIMIT :top_k
        """
    )

def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(a * a for a in left)) or 1.0
    right_norm = sqrt(sum(b * b for b in right)) or 1.0
    return dot / (left_norm * right_norm)

def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in pgvector_storage_values(values)) + "]"


def _elapsed_ms(start_ns: int, collect_timing: bool) -> float:
    if not collect_timing:
        return 0.0
    return (perf_counter_ns() - start_ns) / 1_000_000.0
