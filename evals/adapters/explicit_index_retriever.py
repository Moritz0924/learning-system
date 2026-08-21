"""Evaluation-only vector retriever pinned to completed index-version IDs.

Production retrieval intentionally exposes only active indexes.  Formal ablations
need to compare completed candidate indexes without changing that invariant, so
this adapter lives in ``evals`` and never mutates database state.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from backend.app.application.document_index_service import embedding_client_identity
from backend.app.domain.rag.retrieval import (
    QueryAnalysis,
    RetrievalCandidate,
    RetrievalRequest,
)
from backend.app.models import Document, DocumentChunk, DocumentIndexVersion
from backend.app.infrastructure.persistence.repositories.rag_repository import _vector_literal


class ExplicitIndexVersionError(RuntimeError):
    """The requested evaluation index set is not a safe, completed cohort."""


_COMPLETED_INDEX_STATUSES = frozenset({"ready", "active", "retired"})


@dataclass(slots=True)
class ExplicitIndexVersionVectorRetriever:
    """Read only an exact cohort of completed index versions for Phase 1.

    The retriever performs the same cosine-distance ranking as the production
    vector path but deliberately does *not* require ``status == 'active'``.
    Its constructor validates the exact selected versions up front, which makes
    a missing, failed, incomplete, or identity-mismatched candidate fail closed.
    """

    session: Session
    embedding_client: object
    index_version_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        normalized = tuple(sorted({value.strip() for value in self.index_version_ids if value.strip()}))
        if not normalized or len(normalized) != len(self.index_version_ids):
            raise ExplicitIndexVersionError("exact nonempty index_version_ids are required")
        self.index_version_ids = normalized
        self._validate_versions()

    def retrieve(
        self,
        request: RetrievalRequest,
        *,
        query: str,
        analysis: QueryAnalysis,
    ) -> tuple[RetrievalCandidate, ...]:
        del analysis  # Phase 1 has no rewrite, keyword, metadata, fusion, or reranking path.
        requested_scope = set(request.filters.index_version_ids)
        if requested_scope and requested_scope != set(self.index_version_ids):
            raise ExplicitIndexVersionError("request index scope must equal the explicit evaluation cohort")
        if _uses_pgvector(self.session):
            return self._retrieve_postgresql(request, query=query)
        rows = self.session.execute(
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .join(
                DocumentIndexVersion,
                and_(
                    DocumentIndexVersion.id == DocumentChunk.index_version_id,
                    DocumentIndexVersion.document_id == DocumentChunk.document_id,
                ),
            )
            .where(Document.parse_status == "success")
            .where(DocumentIndexVersion.id.in_(self.index_version_ids))
            .where(_visibility_condition(request.user_id))
            .order_by(DocumentChunk.id)
        ).all()
        if not rows:
            return ()
        query_vector = [float(value) for value in self.embedding_client.embed(query)]
        _, _, dimensions = embedding_client_identity(self.embedding_client)
        if len(query_vector) != dimensions:
            raise ExplicitIndexVersionError(
                f"query embedding dimensions {len(query_vector)} do not match configured {dimensions}"
            )
        ranked = sorted(
            (
                (_cosine_similarity(query_vector, chunk.embedding or []), chunk, document)
                for chunk, document in rows
            ),
            key=lambda item: (-item[0], item[1].id),
        )[: request.top_k]
        return tuple(
            RetrievalCandidate(
                chunk_id=chunk.id,
                document_id=document.id,
                index_version_id=chunk.index_version_id,
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
                retriever="vector",
                query=query,
                rank=rank,
                raw_score=score,
                score_kind="cosine_similarity",
                higher_is_better=True,
            )
            for rank, (score, chunk, document) in enumerate(ranked, start=1)
        )

    def _retrieve_postgresql(
        self,
        request: RetrievalRequest,
        *,
        query: str,
    ) -> tuple[RetrievalCandidate, ...]:
        query_vector = [float(value) for value in self.embedding_client.embed(query)]
        _, _, dimensions = embedding_client_identity(self.embedding_client)
        if len(query_vector) != dimensions:
            raise ExplicitIndexVersionError(
                f"query embedding dimensions {len(query_vector)} do not match configured {dimensions}"
            )
        rows = self.session.execute(
            build_postgresql_explicit_vector_statement(),
            {
                "index_version_ids": list(self.index_version_ids),
                "query_vector": _vector_literal(query_vector),
                "top_k": request.top_k,
                "user_id": request.user_id,
            },
        ).mappings()
        return tuple(
            RetrievalCandidate(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                index_version_id=row["index_version_id"],
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
                retriever="vector",
                query=query,
                rank=rank,
                raw_score=float(row["distance"]),
                score_kind="cosine_distance",
                higher_is_better=False,
            )
            for rank, row in enumerate(rows, start=1)
        )

    def _validate_versions(self) -> None:
        versions = {
            version.id: version
            for version in self.session.scalars(
                select(DocumentIndexVersion).where(
                    DocumentIndexVersion.id.in_(self.index_version_ids)
                )
            ).all()
        }
        missing = sorted(set(self.index_version_ids) - set(versions))
        if missing:
            raise ExplicitIndexVersionError(
                "requested index versions are missing: " + ", ".join(missing)
            )
        provider, model, dimensions = embedding_client_identity(self.embedding_client)
        for version_id in self.index_version_ids:
            version = versions[version_id]
            if version.status not in _COMPLETED_INDEX_STATUSES or version.completed_at is None:
                raise ExplicitIndexVersionError(
                    f"index version {version_id} is not completed and nonfailed"
                )
            identity = (
                version.embedding_provider,
                version.embedding_model,
                version.embedding_dimensions,
            )
            if identity != (provider, model, dimensions):
                raise ExplicitIndexVersionError(
                    f"index version {version_id} embedding identity does not match evaluation provider"
                )


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(a * a for a in left)) or 1.0
    right_norm = sqrt(sum(a * a for a in right)) or 1.0
    return dot / left_norm / right_norm


def _visibility_condition(user_id: str | None):
    if user_id is None:
        return Document.corpus_type == "curated"
    return or_(
        Document.corpus_type == "curated",
        Document.owner_user_id == user_id,
    )


def _uses_pgvector(session: Session) -> bool:
    bind = session.get_bind()
    return bool(bind and bind.dialect.name == "postgresql")


def build_postgresql_explicit_vector_statement():
    """The production pgvector cosine distance with an explicit completed cohort."""
    return text(
        """
        SELECT
            document_chunks.id AS chunk_id,
            document_chunks.document_id AS document_id,
            document_chunks.index_version_id AS index_version_id,
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
        JOIN document_index_versions AS index_version
          ON index_version.id = document_chunks.index_version_id
         AND index_version.document_id = document_chunks.document_id
        WHERE documents.parse_status = 'success'
          AND index_version.id = ANY(CAST(:index_version_ids AS text[]))
          AND index_version.status IN ('ready', 'active', 'retired')
          AND index_version.completed_at IS NOT NULL
          AND document_chunks.embedding_vector IS NOT NULL
          AND (
                documents.corpus_type = 'curated'
                OR (:user_id IS NOT NULL AND documents.owner_user_id = :user_id)
              )
        ORDER BY document_chunks.embedding_vector <=> CAST(:query_vector AS halfvec), document_chunks.id
        LIMIT :top_k
        """
    )


__all__ = [
    "ExplicitIndexVersionError",
    "ExplicitIndexVersionVectorRetriever",
    "build_postgresql_explicit_vector_statement",
]
