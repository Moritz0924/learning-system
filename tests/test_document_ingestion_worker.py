from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from io import BytesIO

import pytest
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import insert, select
from sqlalchemy.dialects import postgresql, sqlite

from backend.app.models import (
    AuthSession,
    Document,
    DocumentChunk,
    OutboxEvent,
    User,
    UserCapabilityBinding,
    UserModelProfile,
)
from backend.app.core.security import auth_settings
from backend.app.infrastructure.auth.jwt_codec import AccessTokenCodec
from backend.app.core.exceptions import DocumentProcessingUnavailable
from backend.app.application.document_service import (
    claim_dispatchable_document_upload_events,
    release_document_upload_event,
)
from backend.app.application.engine import _rag_runtime_mode
from backend.app.services.embeddings import EmbeddingUnavailable
from backend.app.services.embeddings import DeterministicEmbeddingClient
from tests.fakes.secret_store import InMemorySecretStore
from backend.app.services.object_storage import (
    LocalDocumentObjectStorage,
    ObjectStorageUnavailable,
    build_document_object_storage,
)
from backend.app.services.stage3 import (
    DeterministicEmbeddingClient,
    SQLAlchemyRagRepository,
    create_document_record,
    list_document_records,
    process_document_upload_event,
    process_document_upload,
)


@pytest.fixture(autouse=True)
def seed_document_owners(db_session):
    for user_id in ("user-1", "user/with:colon"):
        db_session.add(
            User(
                id=user_id,
                email=f"{user_id.replace('/', '-')}@example.test",
                display_name=user_id,
                status="active",
            )
        )
    db_session.commit()


def _simple_pdf_bytes(text: str) -> bytes:
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 550, 750), text, fontsize=12)
    pdf = document.tobytes()
    document.close()
    return pdf


def _blank_pdf_bytes() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def _multi_page_pdf_bytes(page_count: int) -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def _png_bytes(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color="white").save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize(
    ("filename", "mime_type", "content", "source_format"),
    [
        ("guide.md", "text/markdown", b"# Guide\n\nNative markdown upload.", "markdown"),
        ("notes.txt", "text/plain", b"Native text upload.", "plain_text"),
    ],
)
@pytest.mark.parametrize("feature_enabled", [False, True])
@pytest.mark.parametrize("processing_mode", ["inline", "defer"])
def test_text_upload_production_path_preserves_queued_strategy_and_v3_text_provenance(
    db_session,
    monkeypatch,
    filename,
    mime_type,
    content,
    source_format,
    feature_enabled,
    processing_mode,
):
    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", str(feature_enabled).lower())
    monkeypatch.setenv("EMBEDDING_BACKEND", "deterministic")
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename=filename,
        mime_type=mime_type,
        content_bytes=content,
        processing_mode=processing_mode,
    )
    if processing_mode == "defer":
        event = db_session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.payload_json["document_id"].as_string() == document["id"]
            )
        )
        assert event.payload_json["chunking_execution"]["strategy"] == (
            "hybrid_v3" if feature_enabled else "v2"
        )
        process_document_upload_event(db_session, event_id=event.id)

    stored = db_session.get(Document, document["id"])
    chunk = db_session.scalar(
        select(DocumentChunk).where(DocumentChunk.document_id == document["id"])
    )
    assert stored.parse_status == "success"
    assert chunk is not None
    if feature_enabled:
        assert stored.parser_version == "text-parser-v1"
        assert chunk.metadata_json["chunk_schema_version"] == "v3"
        assert chunk.metadata_json["source_provenance"] == {
            "file_type": ["text"],
            "processing_mode": ["text_native"],
            "source_element": ["text_file"],
            "source_format": [source_format],
        }
    else:
        assert stored.parser_version == "document-parser-v3"
        assert chunk.metadata_json["chunk_schema_version"] == "v2"


@pytest.mark.parametrize(
    ("queued_enabled", "worker_enabled", "expected_strategy", "expected_parser_version"),
    [
        (False, True, "v2", "document-parser-v3"),
        (True, False, "hybrid_v3", "text-parser-v1"),
    ],
)
def test_queued_upload_uses_enqueue_snapshot_after_feature_flag_flips(
    db_session,
    monkeypatch,
    queued_enabled,
    worker_enabled,
    expected_strategy,
    expected_parser_version,
):
    monkeypatch.setenv("EMBEDDING_BACKEND", "deterministic")
    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", str(queued_enabled).lower())
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="queued.md",
        mime_type="text/markdown",
        content_bytes=b"# Queued\n\nFrozen strategy must execute.",
        processing_mode="defer",
    )
    event = db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.payload_json["document_id"].as_string() == document["id"]
        )
    )
    assert event.payload_json["chunking_execution"]["strategy"] == expected_strategy

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", str(worker_enabled).lower())
    result = process_document_upload_event(db_session, event_id=event.id)

    stored = db_session.get(Document, document["id"])
    assert result["status"] == "succeeded"
    assert stored.parser_version == expected_parser_version


def test_legacy_queued_upload_without_snapshot_is_fixed_to_v2(db_session, monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "deterministic")
    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "false")
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="legacy.md",
        mime_type="text/markdown",
        content_bytes=b"# Legacy\n\nNo snapshot means legacy V2.",
        processing_mode="defer",
    )
    event = db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.payload_json["document_id"].as_string() == document["id"]
        )
    )
    event.payload_json = {"document_id": document["id"]}
    db_session.flush()
    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")

    result = process_document_upload_event(db_session, event_id=event.id)

    assert result["status"] == "succeeded"
    assert db_session.get(Document, document["id"]).parser_version == "document-parser-v3"


def test_old_incomplete_v3_snapshot_fails_without_retry_or_v2_fallback(db_session, monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "deterministic")
    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="incompatible.md",
        mime_type="text/markdown",
        content_bytes=b"# Incompatible\n\nThis old snapshot must not run.",
        processing_mode="defer",
    )
    event = db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.payload_json["document_id"].as_string() == document["id"]
        )
    )
    event.payload_json["chunking_execution"] = {
        "strategy": "hybrid_v3",
        "parser_profile": "structured_v3",
        "policy": {},
    }
    db_session.flush()

    result = process_document_upload_event(db_session, event_id=event.id)

    assert result["status"] == "failed"
    assert event.attempts == 1
    assert db_session.get(Document, document["id"]).parse_status == "failed"
    assert db_session.scalar(
        select(DocumentChunk).where(DocumentChunk.document_id == document["id"])
    ) is None


def test_deferred_enqueue_resolves_snapshot_without_constructing_embedding_client(db_session, monkeypatch):
    import backend.app.application.document_chunking_service as chunking_service

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setattr(
        chunking_service,
        "build_embedding_client",
        lambda: (_ for _ in ()).throw(AssertionError("enqueue must not build embeddings")),
    )

    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="pure-config.md",
        mime_type="text/markdown",
        content_bytes=b"# Pure\n\nConfiguration only.",
        processing_mode="defer",
    )

    event = db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.payload_json["document_id"].as_string() == document["id"]
        )
    )
    assert event.payload_json["chunking_execution"]["strategy"] == "hybrid_v3"


def test_document_chunk_embedding_vector_binds_as_pgvector_for_postgres_only():
    statement = insert(DocumentChunk).values(
        id="chunk-1",
        document_id="doc-1",
        chunk_index=1,
        content="Postgres vector chunk",
        token_count=3,
        embedding=[0.1, 0.2, 0.3],
        embedding_vector="[0.10000000,0.20000000,0.30000000]",
        metadata_json={"source_type": "markdown"},
        citation_label="postgres.md chunk 1",
    )

    postgres_sql = str(statement.compile(dialect=postgresql.dialect()))
    sqlite_sql = str(statement.compile(dialect=sqlite.dialect()))

    assert "CAST(%(embedding_vector)s AS vector(1536))" in postgres_sql
    assert "CAST" not in sqlite_sql


def test_rag_retrieval_backend_normalizes_case_and_whitespace(monkeypatch):
    class PostgreSQLSession:
        @staticmethod
        def get_bind():
            return type("PostgreSQLBind", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()})()

    monkeypatch.setenv("RAG_RETRIEVAL_BACKEND", "  PGVECTOR  ")
    repository = SQLAlchemyRagRepository(PostgreSQLSession(), DeterministicEmbeddingClient())

    assert repository._uses_pgvector() is True


def test_rag_runtime_metadata_uses_same_normalized_backend_mode(monkeypatch):
    class PostgreSQLSession:
        @staticmethod
        def get_bind():
            return type("PostgreSQLBind", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()})()

    monkeypatch.setenv("RAG_RETRIEVAL_BACKEND", "  PGVECTOR  ")

    assert _rag_runtime_mode(PostgreSQLSession()) == "pgvector"


def test_outbox_model_declares_dispatch_due_index():
    index_names = {index.name for index in OutboxEvent.__table__.indexes}

    assert "ix_outbox_events_dispatch_due" in index_names


def test_document_processing_resolves_owner_embedding_and_vision_runtime(
    db_session, monkeypatch
):
    """Using environment embedding/vision after an owner binding exists must fail this test."""
    import backend.app.application.document_service as document_service

    document = Document(
        id="doc-owner-runtime",
        owner_user_id="user-1",
        filename="owner.png",
        object_key="owner.png",
        mime_type="image/png",
        parse_status="pending",
        sha256="d" * 64,
    )
    db_session.add(document)
    db_session.add_all(
        [
            UserModelProfile(
                id="doc-vision-profile",
                user_id="user-1",
                name="Document vision",
                capability="vision",
                provider="openai_compatible",
                base_url="https://vision.example.test/v1",
                model_name="vision-model",
            ),
            UserModelProfile(
                id="doc-embedding-profile",
                user_id="user-1",
                name="Document embedding",
                capability="embedding",
                provider="openai_compatible",
                base_url="https://embedding.example.test/v1",
                model_name="embedding-model",
                dimensions=1536,
            ),
        ]
    )
    db_session.flush()
    db_session.add_all(
        [
            UserCapabilityBinding(
                id="doc-vision-binding",
                user_id="user-1",
                capability="vision",
                model_profile_id="doc-vision-profile",
            ),
            UserCapabilityBinding(
                id="doc-embedding-binding",
                user_id="user-1",
                capability="embedding",
                model_profile_id="doc-embedding-profile",
            ),
        ]
    )
    db_session.flush()
    store = InMemorySecretStore()
    vision = object()
    embedding = DeterministicEmbeddingClient()
    resolutions: list[tuple[str, str, object]] = []

    class RecordingResolver:
        def __init__(self, session, *, user_id, secret_store):
            assert session is db_session
            self.user_id = user_id
            self.secret_store = secret_store

        def resolve(self, capability):
            resolutions.append((self.user_id, capability, self.secret_store))
            return vision if capability == "vision" else embedding

    def parse(content_bytes, *, filename, mime_type, ocr_client, vision_client, document_id):
        assert vision_client is vision
        return document_service.ParsedDocumentContent(
            chunks=[{"content": "owner runtime content", "metadata": {"block_index": 1}}],
            page_count=1,
            block_count=1,
            parser_version="document-parser-v3",
        )

    monkeypatch.setattr(document_service, "RuntimeResolver", RecordingResolver, raising=False)
    monkeypatch.setattr(document_service, "_parse_document_content", parse)

    result = document_service.process_document_upload(
        db_session,
        document_id=document.id,
        content_bytes=b"image",
        secret_store=store,
    )

    assert result["status"] == "success"
    assert resolutions == [
        ("user-1", "vision", store),
        ("user-1", "embedding", store),
    ]


def test_document_ingestion_rechecks_embedding_identity_before_activation(
    db_session, monkeypatch
):
    """Activating an index after its bound embedding identity changed must fail this test."""
    import backend.app.application.document_service as document_service

    document = Document(
        id="doc-ingestion-race",
        owner_user_id="user-1",
        filename="race.md",
        object_key="race.md",
        mime_type="text/markdown",
        parse_status="pending",
        sha256="e" * 64,
    )
    profile = UserModelProfile(
        id="doc-race-embedding-profile",
        user_id="user-1",
        name="Race embedding",
        capability="embedding",
        provider="openai_compatible",
        base_url="https://embedding.example.test/v1",
        model_name="embedding-before",
        dimensions=1536,
    )
    db_session.add_all([document, profile])
    db_session.flush()
    db_session.add(
        UserCapabilityBinding(
            id="doc-race-embedding-binding",
            user_id="user-1",
            capability="embedding",
            model_profile_id=profile.id,
        )
    )
    db_session.flush()
    activated = []

    class Resolver:
        def __init__(self, session, *, user_id, secret_store):
            pass

        def resolve(self, capability):
            profile.model_name = "embedding-after"
            db_session.flush()
            return DeterministicEmbeddingClient()

    class RacingIndexService:
        def __init__(self, session, embedding_client):
            pass

        def build_index(self, **kwargs):
            return type("Candidate", (), {"id": "candidate", "status": "ready"})()

        def activate_index(self, **kwargs):
            activated.append(kwargs)

    monkeypatch.setattr(document_service, "RuntimeResolver", Resolver)
    monkeypatch.setattr(document_service, "DocumentIndexService", RacingIndexService)
    monkeypatch.setattr(
        document_service,
        "_parse_document_content",
        lambda *args, **kwargs: document_service.ParsedDocumentContent(
            chunks=[{"content": "race content", "metadata": {"block_index": 1}}],
            page_count=1,
            block_count=1,
            parser_version="document-parser-v3",
        ),
    )

    with pytest.raises(DocumentProcessingUnavailable):
        document_service.process_document_upload(
            db_session,
            document_id=document.id,
            content_bytes=b"race",
            secret_store=InMemorySecretStore(),
        )

    assert activated == []


def test_embedding_identity_recheck_refreshes_cross_session_profile_changes(
    session_factory,
):
    """Reusing cached binding/profile ORM state in the final guard must fail this test."""
    import backend.app.application.document_service as document_service

    with session_factory() as setup:
        setup.add(
            User(
                id="identity-refresh-user",
                email="identity-refresh@example.com",
                normalized_email="identity-refresh@example.com",
                display_name="Identity Refresh",
            )
        )
        setup.add(
            UserModelProfile(
                id="identity-refresh-profile",
                user_id="identity-refresh-user",
                name="Embedding",
                capability="embedding",
                provider="openai_compatible",
                base_url="https://embedding.example.test/v1",
                model_name="embedding-before",
                dimensions=1536,
            )
        )
        setup.flush()
        setup.add(
            UserCapabilityBinding(
                id="identity-refresh-binding",
                user_id="identity-refresh-user",
                capability="embedding",
                model_profile_id="identity-refresh-profile",
            )
        )
        setup.commit()

    with session_factory() as stale_session:
        cached_binding = stale_session.get(UserCapabilityBinding, "identity-refresh-binding")
        cached_profile = stale_session.get(UserModelProfile, "identity-refresh-profile")
        before = document_service._current_embedding_runtime_identity(
            stale_session,
            user_id="identity-refresh-user",
        )
        with session_factory() as writer:
            writer.get(UserModelProfile, "identity-refresh-profile").model_name = "embedding-after"
            writer.commit()
        after = document_service._current_embedding_runtime_identity(
            stale_session,
            user_id="identity-refresh-user",
        )

    assert cached_binding is not None
    assert cached_profile is not None
    assert before != after


def test_inline_creation_and_worker_thread_secret_store(db_session, monkeypatch):
    """Dropping SecretStore at either inline or Celery boundary must fail this test."""
    import backend.app.application.document_service as document_service
    import backend.app.worker as worker

    store = InMemorySecretStore()
    inline_stores: list[object] = []
    worker_stores: list[object] = []

    def inline_process(session, *, document_id, content_bytes, secret_store):
        inline_stores.append(secret_store)
        document = session.get(Document, document_id)
        document.parse_status = "success"
        return {"document_id": document_id, "status": "success", "chunk_count": 0}

    monkeypatch.setattr(document_service, "process_document_upload", inline_process)
    document_service.create_document_record(
        db_session,
        user_id="user-1",
        filename="runtime.md",
        mime_type="text/markdown",
        content="runtime",
        secret_store=store,
    )

    @contextmanager
    def session_local():
        yield db_session

    def event_process(session, *, event_id, secret_store):
        worker_stores.append(secret_store)
        return {"event_id": event_id, "status": "succeeded"}

    monkeypatch.setattr(worker, "SessionLocal", session_local)
    monkeypatch.setattr(worker, "get_secret_store", lambda: store)
    monkeypatch.setattr(worker, "process_document_upload_event", event_process)
    worker.process_document_upload_task("outbox-runtime")

    assert inline_stores == [store]
    assert worker_stores == [store]


def test_local_object_storage_rejects_absolute_object_keys(tmp_path):
    storage = LocalDocumentObjectStorage(root_dir=tmp_path / "objects")
    outside = tmp_path / "outside.txt"

    with pytest.raises(ObjectStorageUnavailable, match="document object key"):
        storage.put_bytes(str(outside), b"escape", content_type="text/plain")

    assert not outside.exists()


def test_document_object_storage_treats_blank_backend_as_default_local(monkeypatch):
    monkeypatch.setenv("DOCUMENT_OBJECT_STORAGE_BACKEND", "   ")
    monkeypatch.setenv("MINIO_ENDPOINT", "   ")

    storage = build_document_object_storage()

    assert isinstance(storage, LocalDocumentObjectStorage)


def test_document_object_storage_treats_blank_minio_config_as_missing(monkeypatch):
    monkeypatch.setenv("DOCUMENT_OBJECT_STORAGE_BACKEND", "minio")
    monkeypatch.setenv("MINIO_ENDPOINT", "   ")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")

    with pytest.raises(ObjectStorageUnavailable, match="MINIO_ENDPOINT"):
        build_document_object_storage()


def test_markdown_upload_registers_pending_then_worker_makes_chunks_searchable(db_session):
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="rag-notes.md",
        mime_type="text/markdown",
        content="# RAG\nWorker chunks become searchable citations.",
        processing_mode="defer",
    )

    assert document["parse_status"] == "pending"

    result = process_document_upload(
        db_session,
        document_id=document["id"],
        content_bytes=b"# RAG\nWorker chunks become searchable citations.",
    )

    assert result == {"document_id": document["id"], "status": "success", "chunk_count": 1}
    stored = db_session.get(Document, document["id"])
    assert stored.parse_status == "success"
    chunks = db_session.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == document["id"])
    ).all()
    metadata = chunks[0].metadata_json
    assert metadata["source_type"] == "markdown"
    assert metadata["untrusted_input"] is True
    assert metadata["chunk_schema_version"] == "v2"
    assert metadata["chunk_id"] == chunks[0].id
    assert metadata["chunk_index"] == 1
    assert metadata["chunk_type"] == "markdown"
    assert metadata["heading_path"] == ["RAG"]
    assert metadata["previous_chunk_id"] is None
    assert metadata["next_chunk_id"] is None
    assert len(metadata["content_hash"]) == 64

    repository = SQLAlchemyRagRepository(db_session, DeterministicEmbeddingClient())
    retrieved = repository.retrieve("searchable citations", user_id="user-1", top_k=1)
    assert retrieved[0].document_id == document["id"]
    assert retrieved[0].source_title == "rag-notes.md"


def test_document_processing_mode_normalizes_case_and_whitespace(db_session):
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="normalized-mode.md",
        mime_type="text/markdown",
        content="# Deferred\nProvider modes should be normalized consistently.",
        processing_mode="  DeFeR  ",
    )

    event = db_session.scalar(
        select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == document["id"])
    )
    assert document["parse_status"] == "pending"
    assert event is not None
    assert db_session.scalar(select(DocumentChunk).where(DocumentChunk.document_id == document["id"])) is None


def test_worker_can_restore_deferred_upload_from_object_storage(db_session):
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="stored-note.md",
        mime_type="text/markdown",
        content="# Stored\nWorker reads this from object storage.",
        processing_mode="defer",
    )

    result = process_document_upload(db_session, document_id=document["id"])

    assert result == {"document_id": document["id"], "status": "success", "chunk_count": 1}
    chunk = db_session.scalar(select(DocumentChunk).where(DocumentChunk.document_id == document["id"]))
    assert "Worker reads this from object storage." in chunk.content


def test_failed_inline_upload_removes_uncommitted_object(db_session, tmp_path, monkeypatch):
    storage = LocalDocumentObjectStorage(root_dir=tmp_path / "objects")
    monkeypatch.setenv("EMBEDDING_BACKEND", "openai")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(DocumentProcessingUnavailable):
        create_document_record(
            db_session,
            user_id="user-1",
            filename="orphan.md",
            mime_type="text/markdown",
            content="# Orphan\nFailed transactions must clean up their private object.",
            processing_mode="inline",
            object_storage=storage,
        )

    db_session.rollback()
    stored_files = [path for path in (tmp_path / "objects").rglob("*") if path.is_file()]
    assert stored_files == []
    assert db_session.scalars(select(Document)).all() == []


def test_document_upload_sanitizes_filename_before_building_object_key(db_session):
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="..\\private/notes.md",
        mime_type="text/markdown",
        content="# Stored\nFilename should not control object paths.",
        processing_mode="defer",
    )

    stored = db_session.get(Document, document["id"])
    assert document["filename"] == "notes.md"
    assert stored.filename == "notes.md"
    assert stored.object_key.startswith("uploads/user-1/")
    assert stored.object_key.endswith("-notes.md")
    assert ".." not in stored.object_key


def test_document_upload_encodes_owner_id_before_building_object_key(db_session):
    document = create_document_record(
        db_session,
        user_id="user/with:colon",
        filename="owner-note.md",
        mime_type="text/markdown",
        content="# Stored\nOwner ids should not control object paths.",
        processing_mode="defer",
    )

    stored = db_session.get(Document, document["id"])
    assert stored.object_key.startswith("uploads/user%2Fwith%3Acolon/")
    assert "user/with:colon" not in stored.object_key


def test_deferred_upload_creates_pending_outbox_event(db_session):
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="outbox-note.md",
        mime_type="text/markdown",
        content="# Outbox\nTrack this upload before worker processing.",
        processing_mode="defer",
    )

    event = db_session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "document.process_upload"))

    assert event is not None
    assert event.status == "pending"
    assert event.attempts == 0
    assert event.payload_json["document_id"] == document["id"]
    assert event.payload_json["chunking_execution"] == {
        "snapshot_version": "chunking-execution-v1",
        "strategy": "v2",
        "parser_profile": "legacy_v2",
        "parser_implementation_version": "legacy-parser-v3",
        "chunking_implementation_version": "chunking-v2",
        "v3_policy": None,
        "policy_fingerprint": None,
        "tokenizer_id": None,
    }


def test_worker_marks_event_failed_when_document_is_missing(db_session):
    event = OutboxEvent(
        id="outbox-missing-document",
        event_type="document.process_upload",
        dedupe_key="document.process_upload:missing-document",
        payload_json={"document_id": "doc-missing"},
        status="pending",
        attempts=0,
    )
    db_session.add(event)
    db_session.commit()

    result = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    assert result["status"] == "failed"
    assert result["error"] == "document doc-missing not found"
    assert event.status == "failed"
    assert event.attempts == 0
    assert event.last_error == "document doc-missing not found"


def test_worker_marks_event_failed_when_payload_is_not_an_object(db_session):
    event = OutboxEvent(
        id="outbox-invalid-payload",
        event_type="document.process_upload",
        dedupe_key="document.process_upload:invalid-payload",
        payload_json=["doc-1"],
        status="pending",
        attempts=0,
    )
    db_session.add(event)
    db_session.commit()

    result = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    assert result == {"event_id": event.id, "status": "failed", "already_processed": False}
    assert event.status == "failed"
    assert event.attempts == 0
    assert event.last_error == "document outbox event missing document_id"


def test_worker_marks_event_failed_for_unexpected_event_type(db_session):
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="wrong-event-type.md",
        mime_type="text/markdown",
        content="# Outbox\nWrong event type must not process this document.",
        processing_mode="defer",
    )
    event = OutboxEvent(
        id="outbox-wrong-event-type",
        event_type="document.delete",
        dedupe_key="document.delete:wrong-event-type",
        payload_json={"document_id": document["id"]},
        status="pending",
        attempts=0,
    )
    db_session.add(event)
    db_session.commit()

    result = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    stored = db_session.get(Document, document["id"])
    chunks = db_session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document["id"])).all()
    assert result["status"] == "failed"
    assert result["error"] == "unexpected document outbox event type: document.delete"
    assert event.status == "failed"
    assert event.attempts == 0
    assert stored.parse_status == "pending"
    assert chunks == []


def test_worker_processes_outbox_event_idempotently(db_session):
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="idempotent-note.md",
        mime_type="text/markdown",
        content="# Idempotent\nOnly one chunk set should survive retries.",
        processing_mode="defer",
    )
    event = db_session.scalar(select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == document["id"]))

    first = process_document_upload_event(db_session, event_id=event.id)
    second = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    chunks = db_session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document["id"])).all()
    assert first["status"] == "succeeded"
    assert second["already_processed"] is True
    assert event.status == "succeeded"
    assert event.attempts == 1
    assert len(chunks) == 1


def test_stale_duplicate_worker_cannot_process_claimed_event_twice(session_factory, monkeypatch):
    import backend.app.application.document_service as document_service

    class CountingEmbeddingClient:
        def __init__(self) -> None:
            self.calls = 0

        def embed(self, text: str) -> list[float]:
            self.calls += 1
            return [0.1] * 1536

    embedding = CountingEmbeddingClient()
    monkeypatch.setattr(document_service, "build_embedding_client", lambda: embedding)

    with session_factory() as setup_session:
        document = create_document_record(
            setup_session,
            user_id="user-1",
            filename="duplicate-delivery.md",
            mime_type="text/markdown",
            content="# Delivery\nCelery may deliver the same event more than once.",
            processing_mode="defer",
        )
        event = setup_session.scalar(
            select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == document["id"])
        )
        event_id = event.id

    with session_factory() as stale_session:
        stale_event = stale_session.get(OutboxEvent, event_id)
        stale_document = stale_session.get(Document, document["id"])
        assert stale_event.status == "pending"
        assert stale_document.parse_status == "pending"

        with session_factory() as winner_session:
            first = process_document_upload_event(winner_session, event_id=event_id)
            winner_session.commit()
        assert stale_event.status == "pending"
        assert stale_document.parse_status == "pending"
        second = process_document_upload_event(stale_session, event_id=event_id)

    assert first["status"] == "succeeded"
    assert second["status"] == "succeeded"
    assert second["already_processed"] is True
    assert embedding.calls == 1


def test_embedding_unavailable_does_not_leave_document_stuck_processing(db_session, monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "openai")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="needs-embedding.md",
        mime_type="text/markdown",
        content="# RAG\nThis parses but cannot be embedded without a key.",
        processing_mode="defer",
    )
    event = db_session.scalar(select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == document["id"]))

    result = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    stored = db_session.get(Document, document["id"])
    assert result["status"] == "pending"
    assert event.status == "pending"
    assert "EMBEDDING_API_KEY" in event.last_error
    assert stored.parse_status == "pending"
    assert stored.parse_error_code == "document.embedding_unavailable"
    assert "EMBEDDING_API_KEY" not in stored.parse_error
    assert db_session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document["id"])).all() == []


def test_v3_permanent_snapshot_failure_is_failed_without_consuming_retry_budget(db_session, monkeypatch):
    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setenv("EMBEDDING_BACKEND", "deterministic")
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="bad-snapshot.md",
        mime_type="text/markdown",
        content="# Snapshot\nThis event has an incompatible V3 execution snapshot.",
        processing_mode="defer",
    )
    event = db_session.scalar(select(OutboxEvent).where(
        OutboxEvent.payload_json["document_id"].as_string() == document["id"]
    ))
    event.payload_json = {
        **event.payload_json,
        "chunking_execution": {
            **event.payload_json["chunking_execution"],
            "parser_implementation_version": "document-parser-v999",
        },
    }
    db_session.flush()

    result = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    stored = db_session.get(Document, document["id"])
    assert result["status"] == "failed"
    assert event.status == "failed"
    assert event.attempts == 1
    assert stored.parse_status == "failed"
    assert stored.parse_error_code == "document.chunking_snapshot_incompatible"
    assert stored.processing_completed_at is not None


def test_v3_invariant_failure_is_failed_without_retrying(db_session, monkeypatch):
    import backend.app.application.document_service as document_service
    from backend.app.domain.rag.chunking.v3.config import ChunkingStrategy
    from backend.app.domain.rag.chunking.v3.errors import HybridChunkingInvariantViolation

    class InvariantFailingService:
        strategy = ChunkingStrategy.HYBRID_V3

        def chunk_upload(self, *args, **kwargs):
            raise HybridChunkingInvariantViolation("final rendered chunk exceeds max_tokens")

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setattr(
        document_service.DocumentChunkingService,
        "from_execution_config",
        lambda *args, **kwargs: InvariantFailingService(),
    )
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="invariant.md",
        mime_type="text/markdown",
        content="# Invariant\nThe V3 invariant must be permanent.",
        processing_mode="defer",
    )
    event = db_session.scalar(select(OutboxEvent).where(
        OutboxEvent.payload_json["document_id"].as_string() == document["id"]
    ))

    result = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    stored = db_session.get(Document, document["id"])
    assert result["status"] == "failed"
    assert event.status == "failed"
    assert event.attempts == 1
    assert stored.parse_status == "failed"
    assert stored.parse_error_code == "document.chunking_invariant_violation"


def test_v3_temporary_embedding_failure_is_rescheduled(db_session, monkeypatch):
    import backend.app.application.document_service as document_service
    from backend.app.domain.rag.chunking.v3.config import ChunkingStrategy
    from backend.app.domain.rag.chunking.v3.errors import SemanticEmbeddingUnavailable

    class EmbeddingFailingService:
        strategy = ChunkingStrategy.HYBRID_V3

        def chunk_upload(self, *args, **kwargs):
            raise SemanticEmbeddingUnavailable("temporary provider timeout")

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setattr(
        document_service.DocumentChunkingService,
        "from_execution_config",
        lambda *args, **kwargs: EmbeddingFailingService(),
    )
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="temporary-v3.md",
        mime_type="text/markdown",
        content="# Temporary\nThe provider can recover on retry.",
        processing_mode="defer",
    )
    event = db_session.scalar(select(OutboxEvent).where(
        OutboxEvent.payload_json["document_id"].as_string() == document["id"]
    ))

    result = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    stored = db_session.get(Document, document["id"])
    assert result["status"] == "pending"
    assert event.status == "pending"
    assert event.attempts == 1
    assert stored.parse_status == "pending"
    assert stored.parse_error_code == "document.embedding_unavailable"


def test_v3_structured_parser_failure_is_failed_without_retrying(db_session, monkeypatch):
    import backend.app.application.document_service as document_service
    from backend.app.domain.rag.chunking.v3.config import ChunkingStrategy
    from backend.app.domain.rag.chunking.v3.errors import StructuredParsingError

    class ParserFailingService:
        strategy = ChunkingStrategy.HYBRID_V3

        def chunk_upload(self, *args, **kwargs):
            raise StructuredParsingError("structured parser produced no usable blocks")

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setattr(
        document_service.DocumentChunkingService,
        "from_execution_config",
        lambda *args, **kwargs: ParserFailingService(),
    )
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="structured-failure.md",
        mime_type="text/markdown",
        content="# Parser\nThe V3 structured parser failure is permanent.",
        processing_mode="defer",
    )
    event = db_session.scalar(select(OutboxEvent).where(
        OutboxEvent.payload_json["document_id"].as_string() == document["id"]
    ))

    result = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    stored = db_session.get(Document, document["id"])
    assert result["status"] == "failed"
    assert event.status == "failed"
    assert event.attempts == 1
    assert stored.parse_status == "failed"
    assert stored.parse_error_code == "document.structured_parser_error"


def test_worker_does_not_retry_pending_event_before_available_at(db_session):
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="scheduled-retry.md",
        mime_type="text/markdown",
        content="# Retry\nThis event should wait until available_at.",
        processing_mode="defer",
    )
    event = db_session.scalar(select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == document["id"]))
    future_available_at = datetime.utcnow() + timedelta(minutes=5)
    event.attempts = 1
    event.available_at = future_available_at
    db_session.commit()

    result = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    stored = db_session.get(Document, document["id"])
    chunks = db_session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document["id"])).all()
    assert result["status"] == "pending"
    assert result["deferred"] is True
    assert result["available_at"] == future_available_at.isoformat()
    assert event.status == "pending"
    assert event.attempts == 1
    assert stored.parse_status == "pending"
    assert chunks == []


def test_worker_schedules_retry_after_recoverable_document_failure(db_session, monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "openai")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("DOCUMENT_PROCESSING_RETRY_DELAY_SECONDS", "30")
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="backoff.md",
        mime_type="text/markdown",
        content="# Retry\nThis event should be rescheduled after a recoverable failure.",
        processing_mode="defer",
    )
    event = db_session.scalar(select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == document["id"]))
    started_at = datetime.utcnow()

    result = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    assert result["status"] == "pending"
    assert event.status == "pending"
    assert event.attempts == 1
    assert event.available_at >= started_at + timedelta(seconds=30)
    assert "EMBEDDING_API_KEY" in event.last_error


def test_partial_embedding_failure_does_not_persist_partial_chunks(db_session, monkeypatch):
    import backend.app.application.document_service as document_service

    class FailsOnSecondChunkEmbeddingClient:
        def __init__(self) -> None:
            self.calls = 0

        def embed(self, text: str) -> list[float]:
            self.calls += 1
            if self.calls == 2:
                raise EmbeddingUnavailable("embedding failed on chunk 2")
            return [0.1] * 1536

    embedding_client = FailsOnSecondChunkEmbeddingClient()
    monkeypatch.setattr(document_service, "build_embedding_client", lambda: embedding_client)
    long_markdown = "# Long\n" + " ".join(f"word{i:03d}" for i in range(160))
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="partial-failure.md",
        mime_type="text/markdown",
        content=long_markdown,
        processing_mode="defer",
    )
    event = db_session.scalar(select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == document["id"]))

    result = process_document_upload_event(db_session, event_id=event.id)

    stored = db_session.get(Document, document["id"])
    chunks = db_session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document["id"])).all()
    assert result["status"] == "pending"
    assert stored.parse_status == "pending"
    assert stored.parse_error_code == "document.embedding_unavailable"
    assert "embedding failed on chunk 2" not in stored.parse_error
    assert chunks == []


def test_wrong_embedding_dimension_does_not_mark_document_success(db_session, monkeypatch):
    import backend.app.application.document_service as document_service

    class WrongDimensionEmbeddingClient:
        def embed(self, text: str) -> list[float]:
            return [0.1, 0.2]

    monkeypatch.setattr(document_service, "build_embedding_client", lambda: WrongDimensionEmbeddingClient())
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="wrong-dim.md",
        mime_type="text/markdown",
        content="# RAG\nRemote embedding returned the wrong shape.",
        processing_mode="defer",
    )
    event = db_session.scalar(select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == document["id"]))

    result = process_document_upload_event(db_session, event_id=event.id)

    stored = db_session.get(Document, document["id"])
    chunks = db_session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document["id"])).all()
    assert result["status"] == "pending"
    assert stored.parse_status == "pending"
    assert stored.parse_error_code == "document.embedding_unavailable"
    assert "expected 1536-dimensional embedding" not in stored.parse_error
    assert chunks == []


def test_worker_dead_letters_repeated_recoverable_document_failures(db_session, monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "openai")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("DOCUMENT_PROCESSING_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("DOCUMENT_PROCESSING_RETRY_DELAY_SECONDS", "0")
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="dead-letter.md",
        mime_type="text/markdown",
        content="# RAG\nThis will keep failing until the worker gives up.",
        processing_mode="defer",
    )
    event = db_session.scalar(select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == document["id"]))

    first = process_document_upload_event(db_session, event_id=event.id)
    second = process_document_upload_event(db_session, event_id=event.id)
    third = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    stored = db_session.get(Document, document["id"])
    assert first["status"] == "pending"
    assert second["status"] == "failed"
    assert third["already_processed"] is True
    assert third["status"] == "failed"
    assert event.status == "failed"
    assert event.attempts == 2
    assert "document processing failed after 2 attempts" in event.last_error
    assert stored.parse_status == "failed"
    assert stored.parse_error_code == "document.processing_attempts_exhausted"
    assert stored.parse_error != event.last_error


def test_celery_upload_claims_outbox_event_before_enqueue(db_session, monkeypatch):
    import backend.app.worker as worker

    queued_event_ids = []
    monkeypatch.setattr(worker.process_document_upload_task, "delay", queued_event_ids.append)

    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="queued-note.md",
        mime_type="text/markdown",
        content="# Queued\nThis should be processed by celery using an outbox event.",
        processing_mode="celery",
    )
    event = db_session.scalar(select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == document["id"]))

    assert event is not None
    assert event.status == "queued"
    assert event.available_at > datetime.utcnow()
    assert queued_event_ids == [event.id]


def test_failed_initial_publish_returns_durable_pending_document_for_periodic_retry(db_session, monkeypatch):
    import backend.app.worker as worker

    monkeypatch.setenv("DOCUMENT_PROCESSING_RETRY_DELAY_SECONDS", "30")

    def fail_delay(*args, **kwargs):
        raise RuntimeError("broker offline")

    monkeypatch.setattr(worker.process_document_upload_task, "delay", fail_delay)
    started_at = datetime.utcnow()

    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="publish-failure.md",
        mime_type="text/markdown",
        content="# Queue\nA durable event must survive broker publication failure.",
        processing_mode="celery",
    )

    event = db_session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "document.process_upload"))
    assert document["parse_status"] == "pending"
    assert event is not None
    assert event.status == "pending"
    assert event.available_at >= started_at + timedelta(seconds=30)
    assert event.last_error == "document event dispatch failed: RuntimeError"


def test_periodic_dispatcher_enqueues_only_due_document_events(session_factory, monkeypatch):
    import backend.app.worker as worker

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    queued_event_ids: list[str] = []
    monkeypatch.setattr(worker.process_document_upload_task, "delay", queued_event_ids.append)

    with session_factory() as session:
        due_document = create_document_record(
            session,
            user_id="user-1",
            filename="due.md",
            mime_type="text/markdown",
            content="# Due\nThis event is ready for dispatch.",
            processing_mode="defer",
        )
        future_document = create_document_record(
            session,
            user_id="user-1",
            filename="future.md",
            mime_type="text/markdown",
            content="# Future\nThis event must wait.",
            processing_mode="defer",
        )
        due_event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == due_document["id"])
        )
        future_event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == future_document["id"])
        )
        future_event.available_at = datetime.utcnow() + timedelta(minutes=5)
        session.commit()

    result = worker.dispatch_pending_document_uploads.run()

    assert result == {"claimed": 1, "dispatched": 1, "failed": 0}
    assert queued_event_ids == [due_event.id]
    with session_factory() as session:
        assert session.get(OutboxEvent, due_event.id).status == "queued"
        assert session.get(OutboxEvent, future_event.id).status == "pending"


def test_stale_dispatch_claim_cannot_release_newer_lease(db_session):
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="lease.md",
        mime_type="text/markdown",
        content="# Lease\nOnly the current dispatcher claim may release this event.",
        processing_mode="defer",
    )
    event = db_session.scalar(
        select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == document["id"])
    )

    first_claim = claim_dispatchable_document_upload_events(db_session, event_id=event.id, limit=1)[0]
    db_session.commit()
    event.available_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()
    second_claim = claim_dispatchable_document_upload_events(db_session, event_id=event.id, limit=1)[0]
    db_session.commit()

    released = release_document_upload_event(
        db_session,
        event_id=event.id,
        lease_token=first_claim.lease_token,
        error_type="OldPublisherFailure",
    )
    db_session.refresh(event)

    assert first_claim.lease_token != second_claim.lease_token
    assert released is False
    assert event.status == "queued"
    assert event.dispatch_token == second_claim.lease_token


def test_document_outbox_dispatch_interval_normalizes_invalid_configuration(monkeypatch):
    import backend.app.worker as worker

    monkeypatch.setenv("DOCUMENT_OUTBOX_DISPATCH_INTERVAL_SECONDS", "   ")
    assert worker._document_outbox_dispatch_interval_seconds() == 15.0

    monkeypatch.setenv("DOCUMENT_OUTBOX_DISPATCH_INTERVAL_SECONDS", "not-a-number")
    assert worker._document_outbox_dispatch_interval_seconds() == 15.0

    monkeypatch.setenv("DOCUMENT_OUTBOX_DISPATCH_INTERVAL_SECONDS", "-2")
    assert worker._document_outbox_dispatch_interval_seconds() == 1.0

    monkeypatch.setenv("DOCUMENT_OUTBOX_DISPATCH_INTERVAL_SECONDS", "NaN")
    assert worker._document_outbox_dispatch_interval_seconds() == 15.0

    monkeypatch.setenv("DOCUMENT_OUTBOX_DISPATCH_INTERVAL_SECONDS", "Infinity")
    assert worker._document_outbox_dispatch_interval_seconds() == 15.0


def test_database_failure_rolls_back_chunk_savepoint_and_preserves_retry_state(db_session, monkeypatch):
    import backend.app.application.document_service as document_service

    monkeypatch.setenv("DOCUMENT_PROCESSING_RETRY_DELAY_SECONDS", "0")

    def store_duplicate_chunks(session, *, document, parsed_chunks):
        common = {
            "id": "forced-duplicate-chunk",
            "document_id": document.id,
            "chunk_index": 1,
            "content": parsed_chunks[0]["content"],
            "token_count": 1,
            "embedding": [0.1] * 1536,
            "embedding_vector": None,
            "metadata_json": {"source_type": "test"},
            "citation_label": "duplicate",
        }
        session.add_all([DocumentChunk(**common), DocumentChunk(**common)])
        session.flush()

    monkeypatch.setattr(document_service, "_store_document_chunks", store_duplicate_chunks)
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="db-failure.md",
        mime_type="text/markdown",
        content="# Database\nA failed chunk write must not poison the retry transaction.",
        processing_mode="defer",
    )
    event = db_session.scalar(
        select(OutboxEvent).where(OutboxEvent.payload_json["document_id"].as_string() == document["id"])
    )

    result = process_document_upload_event(db_session, event_id=event.id)

    db_session.refresh(event)
    stored = db_session.get(Document, document["id"])
    assert result["status"] == "pending"
    assert result["error"] == "document processing database error"
    assert event.status == "pending"
    assert event.attempts == 1
    assert event.last_error == "document processing database error"
    assert stored.parse_status == "pending"
    assert stored.parse_error_code == "document.processing_internal_error"
    assert stored.parse_error == "Document processing failed. Please try again later."
    assert db_session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document["id"])).all() == []
    assert db_session.scalar(select(OutboxEvent.id).where(OutboxEvent.id == event.id)) == event.id


def test_rag_retrieve_returns_no_citations_when_corpus_has_no_chunks(db_session):
    repository = SQLAlchemyRagRepository(db_session, DeterministicEmbeddingClient())

    retrieved = repository.retrieve("needs grounded sources", user_id="user-1", top_k=3)

    assert retrieved == []


def test_pdf_upload_extracts_page_text_and_records_page_metadata(db_session):
    pdf_bytes = _simple_pdf_bytes("PDF RAG retrieval note with reliable searchable lesson content " * 8)
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="rag-guide.pdf",
        mime_type="application/pdf",
        content_bytes=pdf_bytes,
        processing_mode="defer",
    )

    result = process_document_upload(db_session, document_id=document["id"], content_bytes=pdf_bytes)

    assert result["status"] == "success"
    stored = db_session.get(Document, document["id"])
    assert stored.size_bytes == len(pdf_bytes)
    assert stored.page_count == 1
    assert stored.block_count >= 1
    assert stored.parser_version == "document-parser-v3"
    assert stored.processing_started_at is not None
    assert stored.processing_completed_at is not None
    chunk = db_session.scalar(select(DocumentChunk).where(DocumentChunk.document_id == document["id"]))
    assert "PDF RAG retrieval note" in chunk.content
    assert chunk.metadata_json["source_type"] == "uploaded_document"
    assert chunk.metadata_json["processing_source_type"] == "pdf"
    assert chunk.metadata_json["chunk_type"] == "text"
    assert chunk.metadata_json["page_number"] == 1
    assert chunk.metadata_json["text_quality"]["policy_version"] == "pdf-text-quality-v1"
    assert chunk.metadata_json["text_quality"]["selected"] == "native"
    assert chunk.metadata_json["text_quality"]["native"]["quality_sufficient"] is True
    assert chunk.citation_label == "rag-guide.pdf · page 1 · block 1 · chunk 1"


def test_v3_pdf_upload_uses_structured_parser_identity_and_versioned_chunks(db_session, monkeypatch):
    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setenv("EMBEDDING_BACKEND", "deterministic")
    pdf_bytes = _simple_pdf_bytes("structured PDF lesson content with enough native evidence " * 12)
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="v3-rag-guide.pdf",
        mime_type="application/pdf",
        content_bytes=pdf_bytes,
        processing_mode="inline",
    )

    stored = db_session.get(Document, document["id"])
    chunk = db_session.scalar(
        select(DocumentChunk).where(DocumentChunk.document_id == document["id"])
    )

    assert stored.parse_status == "success"
    assert stored.parser_version == "document-parser-v4.1"
    assert chunk.metadata_json["chunk_schema_version"] == "v3"
    assert chunk.metadata_json["source_provenance"]["file_type"] == ["pdf"]
    assert chunk.metadata_json["source_provenance"]["processing_mode"] == ["pdf_text"]


def test_image_upload_uses_ocr_text_for_searchable_chunks(db_session):
    image_bytes = _png_bytes(1, 1)

    class FakeOCRClient:
        def extract_text(self, content: bytes, *, filename: str) -> str:
            assert content == image_bytes
            assert filename == "whiteboard.png"
            return "OCR extracted LangGraph checkpoint notes."

    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="whiteboard.png",
        mime_type="image/png",
        content_bytes=image_bytes,
        processing_mode="defer",
    )

    result = process_document_upload(
        db_session,
        document_id=document["id"],
        content_bytes=image_bytes,
        ocr_client=FakeOCRClient(),
    )

    assert result == {"document_id": document["id"], "status": "success", "chunk_count": 1}
    chunk = db_session.scalar(select(DocumentChunk).where(DocumentChunk.document_id == document["id"]))
    assert "LangGraph checkpoint notes" in chunk.content
    assert chunk.metadata_json["source_type"] == "uploaded_document"
    assert chunk.metadata_json["processing_source_type"] == "image_ocr"
    assert chunk.metadata_json["chunk_type"] == "image_description"


def test_image_ocr_failure_records_user_readable_parse_error(db_session):
    image_bytes = _png_bytes(1, 1)

    class BlankOCRClient:
        def extract_text(self, content: bytes, *, filename: str) -> str:
            return "   "

    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="low-quality.png",
        mime_type="image/png",
        content_bytes=image_bytes,
        processing_mode="defer",
    )

    result = process_document_upload(
        db_session,
        document_id=document["id"],
        content_bytes=image_bytes,
        ocr_client=BlankOCRClient(),
    )

    assert result == {
        "document_id": document["id"],
        "status": "failed",
        "chunk_count": 0,
        "parse_error": "image OCR produced no text",
    }
    stored = db_session.get(Document, document["id"])
    assert stored.parse_status == "failed"
    assert stored.parse_error_code == "document.ocr_no_text"
    assert stored.parse_error == "image OCR produced no text"
    assert stored.processing_completed_at is not None
    listed = list_document_records(db_session, user_id="user-1")
    assert listed[0]["parse_error"] == "image OCR produced no text"


def test_worker_marks_unsupported_upload_failed_without_chunks(db_session):
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="spreadsheet.xls",
        mime_type="application/vnd.ms-excel",
        content_bytes=b"not text",
        processing_mode="defer",
    )

    result = process_document_upload(db_session, document_id=document["id"], content_bytes=b"not text")

    assert result == {
        "document_id": document["id"],
        "status": "failed",
        "chunk_count": 0,
        "parse_error": "unsupported document mime type: application/vnd.ms-excel",
    }
    stored = db_session.get(Document, document["id"])
    assert stored.parse_status == "failed"
    assert stored.parse_error_code == "document.unsupported_type"
    assert stored.parse_error == "unsupported document mime type: application/vnd.ms-excel"
    chunk_count = db_session.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == document["id"])
    ).all()
    assert chunk_count == []


def test_worker_marks_pdf_without_extractable_text_failed_without_chunks(db_session):
    pdf_bytes = _blank_pdf_bytes()
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="blank.pdf",
        mime_type="application/pdf",
        content_bytes=pdf_bytes,
        processing_mode="defer",
    )

    result = process_document_upload(db_session, document_id=document["id"], content_bytes=pdf_bytes)

    assert result == {
        "document_id": document["id"],
        "status": "failed",
        "chunk_count": 0,
        "parse_error": "pdf document contains no extractable text",
    }
    stored = db_session.get(Document, document["id"])
    assert stored.parse_status == "failed"
    assert stored.parse_error_code == "document.parser_no_text"
    assert stored.parse_error == "pdf document contains no extractable text"
    chunks = db_session.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == document["id"])
    ).all()
    assert chunks == []


def test_worker_rejects_pdf_over_configured_page_limit_before_extraction(db_session, monkeypatch):
    monkeypatch.setenv("DOCUMENT_MAX_PDF_PAGES", "1")
    pdf_bytes = _multi_page_pdf_bytes(2)
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="too-many-pages.pdf",
        mime_type="application/pdf",
        content_bytes=pdf_bytes,
        processing_mode="defer",
    )

    result = process_document_upload(db_session, document_id=document["id"], content_bytes=pdf_bytes)

    assert result["status"] == "failed"
    assert result["parse_error"] == "pdf document exceeds 1 page limit"


def test_worker_rejects_pdf_over_extracted_text_limit(db_session, monkeypatch):
    monkeypatch.setenv("DOCUMENT_MAX_EXTRACTED_CHARS", "24")
    pdf_bytes = _simple_pdf_bytes("This extracted PDF text is longer than twenty four characters.")
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="expanded.pdf",
        mime_type="application/pdf",
        content_bytes=pdf_bytes,
        processing_mode="defer",
    )

    result = process_document_upload(db_session, document_id=document["id"], content_bytes=pdf_bytes)

    assert result["status"] == "failed"
    assert result["parse_error"] == "document extracted text exceeds 24 character limit"
    assert db_session.scalar(select(DocumentChunk).where(DocumentChunk.document_id == document["id"])) is None


def test_worker_rejects_document_over_chunk_count_limit_before_embedding(db_session, monkeypatch):
    monkeypatch.setenv("DOCUMENT_MAX_CHUNKS", "1")
    content = "# Many chunks\n" + ("bounded embedding quota " * 80)
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="many-chunks.md",
        mime_type="text/markdown",
        content=content,
        processing_mode="defer",
    )

    result = process_document_upload(
        db_session,
        document_id=document["id"],
        content_bytes=content.encode("utf-8"),
    )

    assert result["status"] == "failed"
    assert result["parse_error"] == "document exceeds 1 chunk limit"
    assert db_session.scalar(select(DocumentChunk).where(DocumentChunk.document_id == document["id"])) is None


def test_worker_rejects_image_over_pixel_limit_before_ocr(db_session, monkeypatch):
    class OCRMustNotRun:
        def extract_text(self, content: bytes, *, filename: str) -> str:
            raise AssertionError("OCR must not run for an oversized image")

    monkeypatch.setenv("DOCUMENT_MAX_IMAGE_PIXELS", "50")
    image_bytes = _png_bytes(10, 10)
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="oversized.png",
        mime_type="image/png",
        content_bytes=image_bytes,
        processing_mode="defer",
    )

    result = process_document_upload(
        db_session,
        document_id=document["id"],
        content_bytes=image_bytes,
        ocr_client=OCRMustNotRun(),
    )

    assert result["status"] == "failed"
    assert result["parse_error"] == "image document exceeds 50 pixel limit"


def _document_user_headers(client, db_session, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    user = db_session.get(User, "user-1")
    assert user is not None
    now = datetime.now()
    session_id = "document-test-session"
    db_session.add(
        AuthSession(
            id=session_id,
            user_id=user.id,
            status="active",
            idle_expires_at=now + timedelta(days=1),
            absolute_expires_at=now + timedelta(days=2),
        )
    )
    db_session.commit()
    token, _ = AccessTokenCodec(auth_settings()).issue(
        user_id=user.id,
        session_id=session_id,
        role=user.role,
        token_version=user.token_version,
    )
    return {"Authorization": f"Bearer {token}"}


def test_upload_endpoint_rejects_empty_content_and_invalid_base64(client, db_session, monkeypatch):
    headers = _document_user_headers(client, db_session, monkeypatch)
    empty_response = client.post(
        "/api/documents/upload",
        headers=headers,
        json={"filename": "empty.md", "mime_type": "text/markdown", "content": ""},
    )
    invalid_response = client.post(
        "/api/documents/upload",
        headers=headers,
        json={
            "filename": "bad.pdf",
            "mime_type": "application/pdf",
            "content_base64": "not valid base64",
        },
    )

    assert empty_response.status_code == 400
    assert empty_response.json()["detail"] == "document upload content is required"
    assert invalid_response.status_code == 400
    assert invalid_response.json()["detail"] == "content_base64 must be valid base64"


def test_upload_endpoint_rejects_decoded_payload_over_configured_limit(client, db_session, monkeypatch):
    monkeypatch.setenv("DOCUMENT_MAX_UPLOAD_BYTES", "4")
    headers = _document_user_headers(client, db_session, monkeypatch)

    response = client.post(
        "/api/documents/upload",
        headers=headers,
        json={
            "filename": "too-large.md",
            "mime_type": "text/markdown",
            "content": "12345",
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "document upload exceeds 4 byte limit"
