from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import select

from backend.app.models import (
    Document,
    DocumentChunk,
    DocumentIndexVersion,
    OutboxEvent,
    User,
    UserCapabilityBinding,
    UserModelProfile,
)
from backend.app.services.embeddings import EmbeddingUnavailable


def _service_module():
    try:
        from backend.app.application import embedding_reindex_service
    except ModuleNotFoundError:
        raise AssertionError("embedding reindex application service is missing") from None
    return embedding_reindex_service


class RecordingEmbeddingClient:
    provider = "profile-provider"
    model = "profile-embedding"
    dimensions = 1536
    mode = "openai"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self.fail:
            raise EmbeddingUnavailable("provider body must stay private")
        return [[0.0] * self.dimensions for _ in texts]


class FakeResolver:
    def __init__(self, client) -> None:
        self.client = client

    def resolve(self, capability: str):
        assert capability == "embedding"
        return self.client


def _seed_reindex_graph(db_session) -> tuple[Document, DocumentIndexVersion]:
    user = User(
        id="reindex-user",
        email="reindex@example.com",
        normalized_email="reindex@example.com",
        display_name="Reindex",
    )
    first = UserModelProfile(
        id="embedding-new",
        user_id=user.id,
        name="New embedding",
        capability="embedding",
        provider="openai_compatible",
        base_url="https://embedding.example/v1",
        model_name="profile-embedding",
        dimensions=1536,
        enabled=True,
    )
    second = UserModelProfile(
        id="embedding-other",
        user_id=user.id,
        name="Other embedding",
        capability="embedding",
        provider="openai_compatible",
        base_url="https://other.example/v1",
        model_name="other-embedding",
        dimensions=1536,
        enabled=True,
    )
    binding = UserCapabilityBinding(
        id="embedding-binding",
        user_id=user.id,
        capability="embedding",
        model_profile_id=first.id,
    )
    document = Document(
        id="reindex-document",
        owner_user_id=user.id,
        filename="notes.md",
        object_key="notes.md",
        mime_type="text/markdown",
        parse_status="success",
        sha256="a" * 64,
    )
    active = DocumentIndexVersion(
        id="old-active-index",
        document_id=document.id,
        build_key="old-build",
        status="active",
        chunker_version="document-parser-v3:chunking-v2",
        embedding_provider="old-provider",
        embedding_model="old-model",
        embedding_dimensions=1536,
        chunk_count=1,
    )
    chunk = DocumentChunk(
        id="old-chunk",
        document_id=document.id,
        index_version_id=active.id,
        chunk_index=1,
        content="Stable source content",
        token_count=3,
        embedding=[1.0] * 1536,
        citation_label="notes.md · block 1 · chunk 1",
        metadata_json={"block_index": 1, "chunk_index": 1},
    )
    db_session.add(user)
    db_session.flush()
    db_session.add_all([first, second])
    db_session.flush()
    db_session.add_all([binding, document])
    db_session.flush()
    db_session.add(active)
    db_session.flush()
    db_session.add(chunk)
    db_session.flush()
    return document, active


def _event(db_session) -> OutboxEvent:
    from backend.app.application.config_service import embedding_profile_identity

    profile_identity = embedding_profile_identity(
        db_session.get(UserModelProfile, "embedding-new")
    )
    event = OutboxEvent(
        id="embedding-reindex-event",
        event_type="document.reindex_embedding",
        dedupe_key="document.reindex_embedding:reindex-user:reindex-document:embedding-new",
        payload_json={
            "user_id": "reindex-user",
            "document_id": "reindex-document",
            "model_profile_id": "embedding-new",
            "embedding_profile_identity": profile_identity,
        },
        status="pending",
    )
    db_session.add(event)
    db_session.flush()
    return event


def test_embedding_binding_enqueue_is_per_successful_document_and_deduplicated(db_session) -> None:
    """Duplicate events or indexing unsuccessful documents must fail this test."""
    service = _service_module()
    _seed_reindex_graph(db_session)
    db_session.add(
        Document(
            id="pending-document",
            owner_user_id="reindex-user",
            filename="pending.md",
            object_key="pending.md",
            mime_type="text/markdown",
            parse_status="pending",
            sha256="b" * 64,
        )
    )
    db_session.flush()

    first = service.enqueue_embedding_reindex_events(
        db_session, user_id="reindex-user", model_profile_id="embedding-new"
    )
    second = service.enqueue_embedding_reindex_events(
        db_session, user_id="reindex-user", model_profile_id="embedding-new"
    )

    assert first == 1
    assert second == 0
    events = db_session.scalars(
        select(OutboxEvent).where(OutboxEvent.event_type == service.EMBEDDING_REINDEX_EVENT_TYPE)
    ).all()
    assert len(events) == 1
    assert events[0].payload_json == {
        "user_id": "reindex-user",
        "document_id": "reindex-document",
        "model_profile_id": "embedding-new",
        "embedding_profile_identity": events[0].payload_json["embedding_profile_identity"],
    }


def test_later_change_back_to_same_embedding_profile_gets_a_new_event(db_session) -> None:
    """Global profile-only dedupe that blocks a later binding change must fail this test."""
    service = _service_module()
    _seed_reindex_graph(db_session)

    service.enqueue_embedding_reindex_events(
        db_session,
        user_id="reindex-user",
        model_profile_id="embedding-new",
        change_id="binding-change-one",
    )
    service.enqueue_embedding_reindex_events(
        db_session,
        user_id="reindex-user",
        model_profile_id="embedding-new",
        change_id="binding-change-two",
    )

    assert len(
        db_session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == service.EMBEDDING_REINDEX_EVENT_TYPE
            )
        ).all()
    ) == 2


def test_reindex_reuses_chunks_and_activates_only_matching_binding(db_session) -> None:
    """Re-parsing content or activating without a matching binding must fail this test."""
    service = _service_module()
    _, old_active = _seed_reindex_graph(db_session)
    event = _event(db_session)
    client = RecordingEmbeddingClient()

    result = service.process_embedding_reindex_event(
        db_session, event_id=event.id, resolver=FakeResolver(client)
    )

    assert result["status"] == "succeeded"
    assert client.calls == [["Stable source content"]]
    db_session.refresh(old_active)
    db_session.refresh(event)
    assert old_active.status == "retired"
    assert event.status == "succeeded"
    active = db_session.scalar(
        select(DocumentIndexVersion).where(
            DocumentIndexVersion.document_id == "reindex-document",
            DocumentIndexVersion.status == "active",
        )
    )
    assert active is not None
    assert active.id != old_active.id
    assert active.chunker_version == old_active.chunker_version


def test_stale_reindex_event_does_not_activate(db_session) -> None:
    """Activating an event after its binding changed must fail this test."""
    service = _service_module()
    _, old_active = _seed_reindex_graph(db_session)
    event = _event(db_session)
    binding = db_session.get(UserCapabilityBinding, "embedding-binding")
    binding.model_profile_id = "embedding-other"
    db_session.flush()
    client = RecordingEmbeddingClient()

    result = service.process_embedding_reindex_event(
        db_session, event_id=event.id, resolver=FakeResolver(client)
    )

    assert result == {"event_id": event.id, "status": "stale"}
    assert client.calls == []
    db_session.refresh(old_active)
    assert old_active.status == "active"


def test_failed_reindex_retains_old_active_index_and_marks_event_failed(db_session) -> None:
    """Retiring the serving index before a successful rebuild must fail this test."""
    service = _service_module()
    _, old_active = _seed_reindex_graph(db_session)
    event = _event(db_session)

    result = service.process_embedding_reindex_event(
        db_session,
        event_id=event.id,
        resolver=FakeResolver(RecordingEmbeddingClient(fail=True)),
    )

    db_session.refresh(old_active)
    db_session.refresh(event)
    assert result["status"] == "failed"
    assert old_active.status == "active"
    assert event.status == "failed"
    assert event.last_error == "embedding_reindex_failed"
    assert "provider body" not in event.last_error


def test_embedding_reindex_outbox_claim_is_single_dispatch(db_session) -> None:
    """Claiming the same pending reindex event twice must fail this test."""
    service = _service_module()
    _seed_reindex_graph(db_session)
    event = _event(db_session)

    first = service.claim_pending_embedding_reindex_events(db_session, limit=10)
    second = service.claim_pending_embedding_reindex_events(db_session, limit=10)

    assert [claim.event_id for claim in first] == [event.id]
    assert second == []
    db_session.refresh(event)
    assert event.status == "dispatched"


def test_worker_embedding_task_invokes_reindex_handler(db_session, monkeypatch) -> None:
    """Dropping the Celery worker entry point must fail this test."""
    from backend.app import worker

    _seed_reindex_graph(db_session)
    event = _event(db_session)
    calls: list[tuple[str, object]] = []

    @contextmanager
    def session_local():
        yield db_session

    def process(session, *, event_id, secret_store):
        calls.append((event_id, secret_store))
        return {"event_id": event_id, "status": "succeeded"}

    monkeypatch.setattr(worker, "SessionLocal", session_local)
    monkeypatch.setattr(worker, "process_embedding_reindex_event", process, raising=False)
    monkeypatch.setattr(worker, "get_secret_store", lambda: "fake-store", raising=False)

    result = worker.process_embedding_reindex_task(event.id)

    assert result == {"event_id": event.id, "status": "succeeded"}
    assert calls == [(event.id, "fake-store")]
