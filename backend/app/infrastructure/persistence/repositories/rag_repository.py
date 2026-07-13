from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import RetrievedChunk
from backend.app.core.runtime_config import runtime_mode
from backend.app.models import Document, DocumentChunk
from backend.app.services.embeddings import EmbeddingUnavailable


@dataclass
class SQLAlchemyRagRepository:
    session: Session
    embedding_client: object
    last_retrieval_status: str = "no_context"
    degraded_reason: str | None = None

    def retrieve(self, query: str, *, top_k: int = 5, user_id: str | None = None) -> list[RetrievedChunk]:
        try:
            with self.session.begin_nested():
                if self._uses_pgvector():
                    chunks = self._retrieve_with_pgvector(query, top_k=top_k, user_id=user_id)
                else:
                    chunks = self._retrieve_with_local_embeddings(query, top_k=top_k, user_id=user_id)
        except EmbeddingUnavailable:
            self.last_retrieval_status = "failed"
            self.degraded_reason = "embedding_unavailable"
            return []
        except SQLAlchemyError:
            self.last_retrieval_status = "failed"
            self.degraded_reason = "retrieval_database_error"
            return []
        self.last_retrieval_status = "grounded" if chunks else "no_context"
        self.degraded_reason = None
        return chunks

    def _retrieve_with_local_embeddings(
        self,
        query: str,
        *,
        top_k: int,
        user_id: str | None,
    ) -> list[RetrievedChunk]:
        visibility_filter = (
            or_(Document.corpus_type == "curated", Document.owner_user_id == user_id)
            if user_id
            else Document.corpus_type == "curated"
        )
        rows = self.session.execute(
            select(DocumentChunk, Document).join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.parse_status == "success")
            .where(visibility_filter)
        ).all()
        if not rows:
            return []
        query_embedding = self.embedding_client.embed(query)
        ranked = sorted(
            rows,
            key=lambda row: _cosine_similarity(query_embedding, row[0].embedding or []),
            reverse=True,
        )
        return [
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
            for chunk, document in ranked[:top_k]
        ]

    def _uses_pgvector(self) -> bool:
        bind = self.session.get_bind()
        return bool(
            bind
            and bind.dialect.name == "postgresql"
            and runtime_mode("RAG_RETRIEVAL_BACKEND", default="pgvector") == "pgvector"
        )

    def _retrieve_with_pgvector(self, query: str, *, top_k: int, user_id: str | None) -> list[RetrievedChunk]:
        from sqlalchemy import text

        query_vector = _vector_literal(self.embedding_client.embed(query))
        owner_clause = "OR documents.owner_user_id = :user_id" if user_id else ""
        rows = self.session.execute(
            text(
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
                    documents.corpus_type AS corpus_type
                FROM document_chunks
                JOIN documents ON documents.id = document_chunks.document_id
                WHERE documents.parse_status = 'success'
                  AND document_chunks.embedding_vector IS NOT NULL
                  AND (documents.corpus_type = 'curated' {owner_clause})
                ORDER BY document_chunks.embedding_vector <=> CAST(:query_vector AS vector)
                LIMIT :top_k
                """
            ),
            {"query_vector": query_vector, "top_k": top_k, "user_id": user_id},
        ).mappings()
        return [
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

def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(a * a for a in left)) or 1.0
    right_norm = sqrt(sum(b * b for b in right)) or 1.0
    return dot / (left_norm * right_norm)

def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"
