from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.app.db import Base
from backend.app.domain.rag.chunking import (
    DEFAULT_CHUNK_POLICY,
    ChunkDraft,
    ChunkMetadataBuilder,
    ChunkType,
)
from backend.app.models import (
    Document,
    DocumentChunk,
    DocumentIndexVersion,
    EmbeddingCacheEntry,
    User,
)
from backend.app.services.embeddings import EmbeddingUnavailable


def test_versioned_index_models_declare_database_invariants() -> None:
    tables = Base.metadata.tables

    assert {"document_index_versions", "embedding_cache_entries"} <= set(tables)

    versions = tables["document_index_versions"]
    assert {
        "id",
        "document_id",
        "build_key",
        "status",
        "chunk_schema_version",
        "chunker_version",
        "embedding_model",
        "embedding_dimensions",
        "chunk_count",
        "error_message",
        "completed_at",
        "activated_at",
        "retired_at",
    } <= set(versions.columns.keys())
    assert {
        "uq_document_index_versions_document_build",
        "uq_document_index_versions_document_id_id",
    } <= {constraint.name for constraint in versions.constraints}
    assert "uq_document_index_versions_active_document" in {
        index.name for index in versions.indexes
    }

    chunks = tables["document_chunks"]
    assert chunks.columns["index_version_id"].nullable is False
    assert {
        "fk_document_chunks_index_document",
        "uq_document_chunks_index_position",
        "ck_document_chunks_positive_index",
    } <= {constraint.name for constraint in chunks.constraints}

    cache = tables["embedding_cache_entries"]
    assert "uq_embedding_cache_model_dimensions_hash" in {
        constraint.name for constraint in cache.constraints
    }


def test_postgresql_index_storage_rejects_non_pgvector_dimensions() -> None:
    module = import_module("backend.app.application.document_index_service")
    assert hasattr(module, "validate_embedding_storage_dimensions")

    module.validate_embedding_storage_dimensions("sqlite", 3)
    with pytest.raises(EmbeddingUnavailable, match="PostgreSQL document indexes require 1536"):
        module.validate_embedding_storage_dimensions("postgresql", 3)


class RecordingBatchEmbeddingClient:
    def __init__(self, *, model: str = "model-a", dimensions: int = 3) -> None:
        self.model = model
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [
            [float((len(text) + offset) % 17) for offset in range(self.dimensions)]
            for text in texts
        ]


class FailingBatchEmbeddingClient(RecordingBatchEmbeddingClient):
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        raise EmbeddingUnavailable("offline embedding failure")


def _index_service(session, embedding_client):
    module = import_module("backend.app.application.document_index_service")
    assert hasattr(module, "DocumentIndexService")
    return module.DocumentIndexService(session, embedding_client)


def _ownership_error():
    module = import_module("backend.app.application.document_index_service")
    assert hasattr(module, "DocumentIndexOwnershipError")
    return module.DocumentIndexOwnershipError


def _seed_document(session, *, document_id: str = "doc-a", owner_user_id: str = "user-a") -> Document:
    if session.get(User, owner_user_id) is None:
        session.add(
            User(
                id=owner_user_id,
                email=f"{owner_user_id}@example.test",
                display_name=owner_user_id,
                status="active",
            )
        )
        session.flush()
    document = Document(
        id=document_id,
        owner_user_id=owner_user_id,
        corpus_type="user_uploaded",
        filename=f"{document_id}.md",
        object_key=f"uploads/{document_id}.md",
        mime_type="text/markdown",
        parse_status="success",
        sha256="a" * 64,
        source_url=None,
        trusted_level=1,
    )
    session.add(document)
    session.flush()
    return document


def _chunks(document_id: str, *contents: str):
    return ChunkMetadataBuilder(DEFAULT_CHUNK_POLICY).build(
        [ChunkDraft(content, ChunkType.TEXT) for content in contents],
        document_id=document_id,
        base_metadata={"source_type": "text"},
    )


def _seed_active_legacy_index(session, document: Document) -> DocumentIndexVersion:
    version = DocumentIndexVersion(
        id=f"index-{document.id}-legacy",
        document_id=document.id,
        build_key="legacy-v1",
        status="active",
        chunk_schema_version="legacy-v1",
        chunker_version="legacy-split-text-v1",
        embedding_model="legacy-model",
        embedding_dimensions=3,
        chunk_count=1,
    )
    session.add(version)
    session.flush()
    session.add(
        DocumentChunk(
            id=f"chunk-{document.id}-legacy",
            document_id=document.id,
            index_version_id=version.id,
            chunk_index=1,
            content="legacy searchable text",
            token_count=3,
            embedding=[1.0, 0.0, 0.0],
            embedding_vector="[1.00000000,0.00000000,0.00000000]",
            metadata_json={"source_type": "text"},
            citation_label=f"{document.filename} · chunk 1",
        )
    )
    session.flush()
    return version


def test_build_is_non_destructive_idempotent_and_namespaces_chunk_ids(db_session) -> None:
    document = _seed_document(db_session)
    legacy = _seed_active_legacy_index(db_session, document)
    client = RecordingBatchEmbeddingClient()
    service = _index_service(db_session, client)
    chunks = _chunks(document.id, "first content", "second content")

    built = service.build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="rebuild-1",
        chunks=chunks,
        chunker_version="chunking-v2",
    )
    retried = service.build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="rebuild-1",
        chunks=chunks,
        chunker_version="chunking-v2",
    )

    assert built.id == retried.id
    assert built.status == "ready"
    assert legacy.status == "active"
    assert client.calls == [["first content", "second content"]]
    assert db_session.scalar(
        select(func.count()).select_from(DocumentIndexVersion).where(
            DocumentIndexVersion.document_id == document.id,
            DocumentIndexVersion.build_key == "rebuild-1",
        )
    ) == 1
    stored = list(
        db_session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.index_version_id == built.id)
            .order_by(DocumentChunk.chunk_index)
        )
    )
    assert [chunk.content for chunk in stored] == ["first content", "second content"]
    assert {chunk.id for chunk in stored}.isdisjoint({chunk.chunk_id for chunk in chunks})
    assert stored[0].metadata_json["next_chunk_id"] == stored[1].id
    assert stored[1].metadata_json["previous_chunk_id"] == stored[0].id
    assert all(chunk.metadata_json["index_version_id"] == built.id for chunk in stored)

    second = service.build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="rebuild-2",
        chunks=chunks,
        chunker_version="chunking-v2",
    )
    second_ids = set(
        db_session.scalars(
            select(DocumentChunk.id).where(DocumentChunk.index_version_id == second.id)
        )
    )
    assert second_ids.isdisjoint({chunk.id for chunk in stored})
    assert client.calls == [["first content", "second content"]]


def test_activation_retires_previous_version_and_rollback_is_atomic(db_session) -> None:
    document = _seed_document(db_session)
    legacy = _seed_active_legacy_index(db_session, document)
    service = _index_service(db_session, RecordingBatchEmbeddingClient())
    ready = service.build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="rebuild-1",
        chunks=_chunks(document.id, "replacement text"),
        chunker_version="chunking-v2",
    )

    activated = service.activate_index(
        user_id="user-a",
        document_id=document.id,
        index_version_id=ready.id,
    )

    db_session.refresh(legacy)
    assert activated.status == "active"
    assert activated.activated_at is not None
    assert legacy.status == "retired"
    assert legacy.retired_at is not None
    assert db_session.scalar(
        select(func.count()).select_from(DocumentIndexVersion).where(
            DocumentIndexVersion.document_id == document.id,
            DocumentIndexVersion.status == "active",
        )
    ) == 1

    rolled_back = service.rollback_index(user_id="user-a", document_id=document.id)

    db_session.refresh(activated)
    assert rolled_back.id == legacy.id
    assert rolled_back.status == "active"
    assert activated.status == "retired"
    assert db_session.scalar(
        select(func.count()).select_from(DocumentIndexVersion).where(
            DocumentIndexVersion.document_id == document.id,
            DocumentIndexVersion.status == "active",
        )
    ) == 1


def test_failed_build_keeps_existing_active_index_and_discards_partial_chunks(db_session) -> None:
    document = _seed_document(db_session)
    legacy = _seed_active_legacy_index(db_session, document)
    service = _index_service(db_session, FailingBatchEmbeddingClient())

    failed = service.build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="failed-rebuild",
        chunks=_chunks(document.id, "cannot embed"),
        chunker_version="chunking-v2",
    )

    db_session.refresh(legacy)
    assert failed.status == "failed"
    assert failed.error_message == "offline embedding failure"
    assert legacy.status == "active"
    assert db_session.scalar(
        select(func.count()).select_from(DocumentChunk).where(
            DocumentChunk.index_version_id == failed.id
        )
    ) == 0


def test_embedding_cache_batches_unique_misses_and_isolates_model_and_dimensions(db_session) -> None:
    document = _seed_document(db_session)
    chunks = _chunks(document.id, "same content", "different content", "same content")
    first_client = RecordingBatchEmbeddingClient(model="model-a", dimensions=3)

    first = _index_service(db_session, first_client).build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="cache-1",
        chunks=chunks,
        chunker_version="chunking-v2",
    )
    same_client = RecordingBatchEmbeddingClient(model="model-a", dimensions=3)
    _index_service(db_session, same_client).build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="cache-2",
        chunks=chunks,
        chunker_version="chunking-v2",
    )
    other_model = RecordingBatchEmbeddingClient(model="model-b", dimensions=3)
    _index_service(db_session, other_model).build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="cache-3",
        chunks=chunks,
        chunker_version="chunking-v2",
    )
    other_dimensions = RecordingBatchEmbeddingClient(model="model-a", dimensions=4)
    _index_service(db_session, other_dimensions).build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="cache-4",
        chunks=chunks,
        chunker_version="chunking-v2",
    )

    assert first.status == "ready"
    assert first_client.calls == [["same content", "different content"]]
    assert same_client.calls == []
    assert other_model.calls == [["same content", "different content"]]
    assert other_dimensions.calls == [["same content", "different content"]]
    assert db_session.scalar(select(func.count()).select_from(EmbeddingCacheEntry)) == 6


def test_dimension_mismatch_fails_build_without_polluting_cache(db_session) -> None:
    document = _seed_document(db_session)

    class WrongDimensionsClient(RecordingBatchEmbeddingClient):
        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 2.0, 3.0], [1.0, 2.0]]

    failed = _index_service(db_session, WrongDimensionsClient(dimensions=3)).build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="wrong-dimensions",
        chunks=_chunks(document.id, "valid first", "wrong dimension"),
        chunker_version="chunking-v2",
    )

    assert failed.status == "failed"
    assert "expected 3-dimensional embedding" in failed.error_message
    assert db_session.scalar(select(func.count()).select_from(EmbeddingCacheEntry)) == 0


def test_failed_idempotent_build_can_retry_without_creating_a_new_version(db_session) -> None:
    document = _seed_document(db_session)

    class FlakyClient(RecordingBatchEmbeddingClient):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            self.attempts += 1
            if self.attempts == 1:
                raise EmbeddingUnavailable("temporary failure")
            return super().embed_batch(texts)

    client = FlakyClient()
    service = _index_service(db_session, client)
    first = service.build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="retryable-build",
        chunks=_chunks(document.id, "retry content"),
        chunker_version="chunking-v2",
    )
    retried = service.build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="retryable-build",
        chunks=_chunks(document.id, "retry content"),
        chunker_version="chunking-v2",
    )

    assert first.id == retried.id
    assert retried.status == "ready"
    assert client.attempts == 2
    assert db_session.scalar(
        select(func.count()).select_from(DocumentIndexVersion).where(
            DocumentIndexVersion.document_id == document.id,
            DocumentIndexVersion.build_key == "retryable-build",
        )
    ) == 1


def test_stale_building_idempotent_version_is_safely_rebuilt(db_session) -> None:
    document = _seed_document(db_session)
    stale = DocumentIndexVersion(
        id="index-stale-building",
        document_id=document.id,
        build_key="stale-build",
        status="building",
        chunk_schema_version="v2",
        chunker_version="chunking-v2",
        embedding_model="model-a",
        embedding_dimensions=3,
        chunk_count=0,
        updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(stale)
    db_session.flush()
    client = RecordingBatchEmbeddingClient()

    rebuilt = _index_service(db_session, client).build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="stale-build",
        chunks=_chunks(document.id, "recovered content"),
        chunker_version="chunking-v2",
    )

    assert rebuilt.id == stale.id
    assert rebuilt.status == "ready"
    assert client.calls == [["recovered content"]]


def test_index_lifecycle_is_document_owner_scoped(db_session) -> None:
    document = _seed_document(db_session)
    _seed_document(db_session, document_id="doc-b", owner_user_id="user-b")
    service = _index_service(db_session, RecordingBatchEmbeddingClient())

    with pytest.raises(_ownership_error()):
        service.build_index(
            user_id="user-b",
            document_id=document.id,
            build_key="foreign-build",
            chunks=_chunks(document.id, "private text"),
            chunker_version="chunking-v2",
        )

    ready = service.build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="owned-build",
        chunks=_chunks(document.id, "private text"),
        chunker_version="chunking-v2",
    )
    with pytest.raises(_ownership_error()):
        service.activate_index(
            user_id="user-b",
            document_id=document.id,
            index_version_id=ready.id,
        )
    with pytest.raises(_ownership_error()):
        service.rollback_index(user_id="user-b", document_id=document.id)


def test_database_rejects_two_active_indexes_for_one_document(db_session) -> None:
    document = _seed_document(db_session)
    service = _index_service(db_session, RecordingBatchEmbeddingClient())
    first = service.build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="race-a",
        chunks=_chunks(document.id, "first"),
        chunker_version="chunking-v2",
    )
    second = service.build_index(
        user_id="user-a",
        document_id=document.id,
        build_key="race-b",
        chunks=_chunks(document.id, "second"),
        chunker_version="chunking-v2",
    )
    first.status = "active"
    second.status = "active"

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_document_processing_builds_and_activates_a_versioned_index(
    db_session,
    monkeypatch,
) -> None:
    import backend.app.application.document_service as document_service

    document = _seed_document(db_session)
    document.parse_status = "pending"
    db_session.flush()
    embedding_client = RecordingBatchEmbeddingClient()
    monkeypatch.setattr(
        document_service,
        "build_embedding_client",
        lambda: embedding_client,
    )

    result = document_service.process_document_upload(
        db_session,
        document_id=document.id,
        content_bytes=b"# Versioned\nThe active index is swapped only after the build completes.",
    )

    assert result == {"document_id": document.id, "status": "success", "chunk_count": 1}
    versions = list(
        db_session.scalars(
            select(DocumentIndexVersion).where(
                DocumentIndexVersion.document_id == document.id
            )
        )
    )
    assert len(versions) == 1
    assert versions[0].status == "active"
    chunks = list(
        db_session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
    )
    assert len(chunks) == 1
    assert chunks[0].index_version_id == versions[0].id
    assert chunks[0].metadata_json["index_version_id"] == versions[0].id
    assert chunks[0].metadata_json["chunk_id"] == chunks[0].id
    assert embedding_client.calls == [[chunks[0].content]]


def test_document_rebuild_keeps_successful_active_index_available_until_activation(
    db_session,
    monkeypatch,
) -> None:
    import backend.app.application.document_service as document_service

    document = _seed_document(db_session)
    legacy = _seed_active_legacy_index(db_session, document)

    class AvailabilityCheckingClient(RecordingBatchEmbeddingClient):
        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            db_session.refresh(document)
            db_session.refresh(legacy)
            assert document.parse_status == "success"
            assert legacy.status == "active"
            return super().embed_batch(texts)

    monkeypatch.setattr(
        document_service,
        "build_embedding_client",
        lambda: AvailabilityCheckingClient(),
    )

    result = document_service.process_document_upload(
        db_session,
        document_id=document.id,
        content_bytes=b"# Rebuild\nThe serving index remains available during a candidate build.",
    )

    db_session.refresh(legacy)
    assert result["status"] == "success"
    assert legacy.status == "retired"
