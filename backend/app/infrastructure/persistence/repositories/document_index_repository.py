from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Iterable

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models import (
    Document,
    DocumentChunk,
    DocumentIndexVersion,
    EmbeddingCacheEntry,
)


class DocumentIndexOwnershipError(PermissionError):
    pass


class DocumentIndexStateError(RuntimeError):
    pass


def deterministic_index_version_id(*, document_id: str, build_key: str) -> str:
    identity = f"document-index-v2\0{document_id}\0{build_key}".encode("utf-8")
    return f"index-{sha256(identity).hexdigest()[:32]}"


def deterministic_embedding_cache_id(
    *,
    content_hash: str,
    embedding_model: str,
    dimensions: int,
) -> str:
    identity = f"embedding-cache-v1\0{embedding_model}\0{dimensions}\0{content_hash}".encode(
        "utf-8"
    )
    return f"embedding-{sha256(identity).hexdigest()[:32]}"


class SQLAlchemyDocumentIndexRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def owned_document(
        self,
        *,
        document_id: str,
        user_id: str | None,
        lock: bool = False,
    ) -> Document:
        statement = select(Document).where(Document.id == document_id)
        if lock:
            statement = statement.with_for_update()
        document = self.session.scalar(statement)
        if document is None:
            raise LookupError(f"document {document_id} not found")
        if document.owner_user_id != user_id:
            raise DocumentIndexOwnershipError("document index is not owned by the requesting user")
        return document

    def begin_build(
        self,
        *,
        document_id: str,
        build_key: str,
        chunker_version: str,
        embedding_model: str,
        embedding_dimensions: int,
    ) -> tuple[DocumentIndexVersion, bool]:
        existing = self.session.scalar(
            select(DocumentIndexVersion).where(
                DocumentIndexVersion.document_id == document_id,
                DocumentIndexVersion.build_key == build_key,
            )
        )
        if existing is not None:
            return existing, False

        version = DocumentIndexVersion(
            id=deterministic_index_version_id(document_id=document_id, build_key=build_key),
            document_id=document_id,
            build_key=build_key,
            status="building",
            chunk_schema_version="v2",
            chunker_version=chunker_version,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            chunk_count=0,
        )
        try:
            with self.session.begin_nested():
                self.session.add(version)
                self.session.flush([version])
        except IntegrityError:
            existing = self.session.scalar(
                select(DocumentIndexVersion).where(
                    DocumentIndexVersion.document_id == document_id,
                    DocumentIndexVersion.build_key == build_key,
                )
            )
            if existing is None:
                raise
            return existing, False
        return version, True

    def cached_embeddings(
        self,
        *,
        content_hashes: Iterable[str],
        embedding_model: str,
        dimensions: int,
    ) -> dict[str, list[float]]:
        hashes = set(content_hashes)
        if not hashes:
            return {}
        rows = self.session.scalars(
            select(EmbeddingCacheEntry).where(
                EmbeddingCacheEntry.embedding_model == embedding_model,
                EmbeddingCacheEntry.dimensions == dimensions,
                EmbeddingCacheEntry.content_hash.in_(hashes),
            )
        )
        return {row.content_hash: [float(value) for value in row.embedding] for row in rows}

    def cache_embedding(
        self,
        *,
        content_hash: str,
        embedding_model: str,
        dimensions: int,
        embedding: list[float],
    ) -> EmbeddingCacheEntry:
        existing = self.session.scalar(
            select(EmbeddingCacheEntry).where(
                EmbeddingCacheEntry.embedding_model == embedding_model,
                EmbeddingCacheEntry.dimensions == dimensions,
                EmbeddingCacheEntry.content_hash == content_hash,
            )
        )
        if existing is not None:
            return existing
        entry = EmbeddingCacheEntry(
            id=deterministic_embedding_cache_id(
                content_hash=content_hash,
                embedding_model=embedding_model,
                dimensions=dimensions,
            ),
            content_hash=content_hash,
            embedding_model=embedding_model,
            dimensions=dimensions,
            embedding=embedding,
        )
        try:
            with self.session.begin_nested():
                self.session.add(entry)
                self.session.flush([entry])
        except IntegrityError:
            existing = self.session.scalar(
                select(EmbeddingCacheEntry).where(
                    EmbeddingCacheEntry.embedding_model == embedding_model,
                    EmbeddingCacheEntry.dimensions == dimensions,
                    EmbeddingCacheEntry.content_hash == content_hash,
                )
            )
            if existing is None:
                raise
            return existing
        return entry

    def finish_build(
        self,
        *,
        version: DocumentIndexVersion,
        chunks: list[DocumentChunk],
    ) -> DocumentIndexVersion:
        if version.status != "building":
            raise DocumentIndexStateError("only a building index can be completed")
        self.session.add_all(chunks)
        version.status = "ready"
        version.chunk_count = len(chunks)
        version.error_message = None
        version.completed_at = _utcnow()
        self.session.flush()
        return version

    def restart_incomplete_build(
        self,
        *,
        version: DocumentIndexVersion,
    ) -> DocumentIndexVersion:
        if version.status not in {"failed", "building"}:
            raise DocumentIndexStateError("only a failed or stale building index can be retried")
        self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.index_version_id == version.id)
        )
        version.status = "building"
        version.chunk_count = 0
        version.error_message = None
        version.completed_at = None
        self.session.flush()
        return version

    def fail_build(
        self,
        *,
        version: DocumentIndexVersion,
        error_message: str,
    ) -> DocumentIndexVersion:
        self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.index_version_id == version.id)
        )
        version.status = "failed"
        version.chunk_count = 0
        version.error_message = error_message
        version.completed_at = _utcnow()
        self.session.flush()
        return version

    def activate(
        self,
        *,
        user_id: str | None,
        document_id: str,
        index_version_id: str,
    ) -> DocumentIndexVersion:
        self.owned_document(document_id=document_id, user_id=user_id, lock=True)
        target = self.session.scalar(
            select(DocumentIndexVersion)
            .where(
                DocumentIndexVersion.id == index_version_id,
                DocumentIndexVersion.document_id == document_id,
            )
            .with_for_update()
        )
        if target is None:
            raise LookupError(f"document index {index_version_id} not found")
        if target.status == "active":
            return target
        if target.status not in {"ready", "retired"}:
            raise DocumentIndexStateError(
                f"document index {index_version_id} cannot be activated from {target.status}"
            )

        now = _utcnow()
        self.session.execute(
            update(DocumentIndexVersion)
            .where(
                DocumentIndexVersion.document_id == document_id,
                DocumentIndexVersion.status == "active",
                DocumentIndexVersion.id != target.id,
            )
            .values(status="retired", retired_at=now)
            .execution_options(synchronize_session="fetch")
        )
        self.session.flush()
        target.status = "active"
        target.activated_at = now
        target.retired_at = None
        self.session.flush()
        return target

    def rollback(
        self,
        *,
        user_id: str | None,
        document_id: str,
        index_version_id: str | None = None,
    ) -> DocumentIndexVersion:
        self.owned_document(document_id=document_id, user_id=user_id, lock=True)
        statement = select(DocumentIndexVersion).where(
            DocumentIndexVersion.document_id == document_id,
            DocumentIndexVersion.status == "retired",
        )
        if index_version_id is not None:
            statement = statement.where(DocumentIndexVersion.id == index_version_id)
        else:
            statement = statement.order_by(
                DocumentIndexVersion.retired_at.desc(),
                DocumentIndexVersion.activated_at.desc(),
                DocumentIndexVersion.created_at.desc(),
                DocumentIndexVersion.id.desc(),
            )
        target = self.session.scalar(statement.limit(1).with_for_update())
        if target is None:
            raise DocumentIndexStateError("no retired document index is available for rollback")
        return self.activate(
            user_id=user_id,
            document_id=document_id,
            index_version_id=target.id,
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
