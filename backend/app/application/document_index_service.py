from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Mapping, Sequence

from sqlalchemy.orm import Session

from backend.app.domain.rag.chunking import Chunk, chunk_content_hash, normalize_chunk_text
from backend.app.infrastructure.persistence.repositories.document_index_repository import (
    DocumentIndexOwnershipError,
    DocumentIndexStateError,
    SQLAlchemyDocumentIndexRepository,
)
from backend.app.infrastructure.persistence.repositories.rag_repository import _vector_literal
from backend.app.models import Document, DocumentChunk, DocumentIndexVersion
from backend.app.services.embeddings import EmbeddingUnavailable


PGVECTOR_DOCUMENT_DIMENSIONS = 1536
STALE_BUILD_AFTER = timedelta(minutes=15)


class DocumentIndexService:
    def __init__(self, session: Session, embedding_client: object) -> None:
        self.session = session
        self.embedding_client = embedding_client
        self.repository = SQLAlchemyDocumentIndexRepository(session)

    def build_index(
        self,
        *,
        user_id: str | None,
        document_id: str,
        build_key: str,
        chunks: Sequence[Chunk | Mapping[str, object]],
        chunker_version: str,
    ) -> DocumentIndexVersion:
        document = self.repository.owned_document(
            document_id=document_id,
            user_id=user_id,
        )
        normalized_key = build_key.strip()
        if not normalized_key:
            raise ValueError("document index build_key is required")
        if len(normalized_key) > 128:
            raise ValueError("document index build_key exceeds 128 characters")
        model = _embedding_model(self.embedding_client)
        dimensions = _embedding_dimensions(self.embedding_client)
        version, created = self.repository.begin_build(
            document_id=document_id,
            build_key=normalized_key,
            chunker_version=chunker_version.strip() or "chunking-v2",
            embedding_model=model,
            embedding_dimensions=dimensions,
        )
        attempt_token = version.build_attempt
        if not created:
            retryable = version.status == "failed" or (
                version.status == "building" and _is_stale_build(version)
            )
            if not retryable:
                return version
            claim = self.repository.restart_incomplete_build(version=version)
            if not claim.claimed:
                return claim.version
            version = claim.version
            if claim.attempt_token is None:
                raise DocumentIndexStateError(
                    "claimed document index build has no attempt token"
                )
            attempt_token = claim.attempt_token

        try:
            bind = self.session.get_bind()
            validate_embedding_storage_dimensions(bind.dialect.name, dimensions)
            prepared = [_prepared_chunk(chunk) for chunk in chunks]
            if not prepared:
                raise ValueError("document index requires at least one chunk")
            content_hashes = [item[1] for item in prepared]
            embeddings = self.repository.cached_embeddings(
                content_hashes=content_hashes,
                embedding_model=model,
                dimensions=dimensions,
            )
            missing_hashes: list[str] = []
            content_by_hash: dict[str, str] = {}
            for content, content_hash, _ in prepared:
                content_by_hash.setdefault(content_hash, content)
                if content_hash not in embeddings and content_hash not in missing_hashes:
                    missing_hashes.append(content_hash)
            if missing_hashes:
                batch = _embed_batch(
                    self.embedding_client,
                    [content_by_hash[content_hash] for content_hash in missing_hashes],
                )
                if len(batch) != len(missing_hashes):
                    raise EmbeddingUnavailable(
                        f"embedding provider returned {len(batch)} vectors for {len(missing_hashes)} chunks"
                    )
                validated_vectors: list[tuple[str, list[float]]] = []
                for content_hash, values in zip(missing_hashes, batch):
                    vector = [float(value) for value in values]
                    if len(vector) != dimensions:
                        raise EmbeddingUnavailable(
                            f"expected {dimensions}-dimensional embedding, got {len(vector)}"
                        )
                    validated_vectors.append((content_hash, vector))
                for content_hash, vector in validated_vectors:
                    self.repository.cache_embedding(
                        content_hash=content_hash,
                        embedding_model=model,
                        dimensions=dimensions,
                        embedding=vector,
                    )
                    embeddings[content_hash] = vector

            chunk_ids = [
                _versioned_chunk_id(
                    index_version_id=version.id,
                    chunk_index=index,
                    content_hash=content_hash,
                )
                for index, (_, content_hash, _) in enumerate(prepared, start=1)
            ]
            records: list[DocumentChunk] = []
            for offset, ((content, content_hash, raw_metadata), chunk_id) in enumerate(
                zip(prepared, chunk_ids)
            ):
                chunk_index = offset + 1
                metadata = {
                    **raw_metadata,
                    "chunk_schema_version": "v2",
                    "chunk_id": chunk_id,
                    "chunk_index": chunk_index,
                    "content_hash": content_hash,
                    "previous_chunk_id": chunk_ids[offset - 1] if offset else None,
                    "next_chunk_id": chunk_ids[offset + 1] if offset + 1 < len(chunk_ids) else None,
                    "index_version_id": version.id,
                    "embedding_model": model,
                    "embedding_dimensions": dimensions,
                    "untrusted_input": True,
                }
                vector = embeddings[content_hash]
                records.append(
                    DocumentChunk(
                        id=chunk_id,
                        document_id=document.id,
                        index_version_id=version.id,
                        chunk_index=chunk_index,
                        content=content,
                        token_count=len(content.split()),
                        embedding=vector,
                        embedding_vector=_vector_literal(vector),
                        metadata_json=metadata,
                        citation_label=_citation_label(
                            document=document,
                            metadata=metadata,
                            chunk_index=chunk_index,
                        ),
                    )
                )
            return self.repository.finish_build(
                version=version,
                attempt_token=attempt_token,
                chunks=records,
            )
        except (EmbeddingUnavailable, ValueError) as exc:
            return self.repository.fail_build(
                version=version,
                attempt_token=attempt_token,
                error_message=str(exc),
            )

    def activate_index(
        self,
        *,
        user_id: str | None,
        document_id: str,
        index_version_id: str,
    ) -> DocumentIndexVersion:
        return self.repository.activate(
            user_id=user_id,
            document_id=document_id,
            index_version_id=index_version_id,
        )

    def rollback_index(
        self,
        *,
        user_id: str | None,
        document_id: str,
        index_version_id: str | None = None,
    ) -> DocumentIndexVersion:
        return self.repository.rollback(
            user_id=user_id,
            document_id=document_id,
            index_version_id=index_version_id,
        )


def document_index_build_key(
    *,
    document_sha256: str,
    chunker_version: str,
    embedding_model: str,
    embedding_dimensions: int,
) -> str:
    identity = (
        f"document-index-build-v2\0{document_sha256}\0{chunker_version}\0"
        f"{embedding_model}\0{embedding_dimensions}"
    ).encode("utf-8")
    return f"v2-{sha256(identity).hexdigest()}"


def embedding_client_identity(client: object) -> tuple[str, int]:
    return _embedding_model(client), _embedding_dimensions(client)


def validate_embedding_storage_dimensions(dialect_name: str, dimensions: int) -> None:
    if dialect_name == "postgresql" and dimensions != PGVECTOR_DOCUMENT_DIMENSIONS:
        raise EmbeddingUnavailable(
            "PostgreSQL document indexes require 1536-dimensional embeddings"
        )


def _is_stale_build(version: DocumentIndexVersion) -> bool:
    updated_at = version.updated_at or version.created_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return updated_at <= datetime.now(timezone.utc) - STALE_BUILD_AFTER


def _prepared_chunk(
    chunk: Chunk | Mapping[str, object],
) -> tuple[str, str, dict[str, object]]:
    if isinstance(chunk, Chunk):
        content = chunk.content
        declared_hash = chunk.content_hash
        metadata = dict(chunk.metadata)
    else:
        content = str(chunk.get("content", ""))
        metadata_value = chunk.get("metadata", {})
        metadata = dict(metadata_value) if isinstance(metadata_value, Mapping) else {}
        declared_hash = str(metadata.get("content_hash", ""))
    normalized = normalize_chunk_text(content)
    if not normalized:
        raise ValueError("document chunk content is required")
    actual_hash = chunk_content_hash(normalized)
    if declared_hash and declared_hash != actual_hash:
        raise ValueError("document chunk content_hash does not match normalized content")
    return normalized, actual_hash, metadata


def _embed_batch(client: object, texts: list[str]) -> list[list[float]]:
    embed_batch = getattr(client, "embed_batch", None)
    if callable(embed_batch):
        return list(embed_batch(texts))
    embed = getattr(client, "embed", None)
    if not callable(embed):
        raise EmbeddingUnavailable("embedding client must provide embed_batch or embed")
    return [list(embed(text)) for text in texts]


def _embedding_model(client: object) -> str:
    value = getattr(client, "model", None) or getattr(client, "mode", None)
    normalized = str(value or "unknown-embedding-model").strip()
    return normalized or "unknown-embedding-model"


def _embedding_dimensions(client: object) -> int:
    try:
        dimensions = int(getattr(client, "dimensions", 1536))
    except (TypeError, ValueError) as exc:
        raise EmbeddingUnavailable("embedding dimensions must be a positive integer") from exc
    if dimensions <= 0:
        raise EmbeddingUnavailable("embedding dimensions must be a positive integer")
    return dimensions


def _versioned_chunk_id(
    *,
    index_version_id: str,
    chunk_index: int,
    content_hash: str,
) -> str:
    identity = f"chunk-v2\0{index_version_id}\0{chunk_index}\0{content_hash}".encode("utf-8")
    return f"chunk-{sha256(identity).hexdigest()[:32]}"


def _citation_label(
    *,
    document: Document,
    metadata: Mapping[str, object],
    chunk_index: int,
) -> str:
    page_number = metadata.get("page_number")
    block_index = metadata.get("block_index", 1)
    local_chunk_index = metadata.get("chunk_index", chunk_index)
    file_type = metadata.get("file_type")
    location = "image" if file_type == "image" else ("slide" if file_type == "pptx" else "page")
    label = f"{document.filename} · {location}"
    if location != "image":
        label += f" {page_number}"
    return f"{label} · block {block_index} · chunk {local_chunk_index}"


__all__ = [
    "DocumentIndexOwnershipError",
    "DocumentIndexService",
    "DocumentIndexStateError",
    "document_index_build_key",
    "embedding_client_identity",
    "validate_embedding_storage_dimensions",
]
