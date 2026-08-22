from __future__ import annotations

from sqlalchemy import select

from backend.app.application.document_service import (
    create_document_record,
    process_document_upload,
    process_document_upload_event,
)
import pytest

from backend.app.models import Document, OutboxEvent, User
from backend.app.services.embeddings import EmbeddingUnavailable


@pytest.fixture(autouse=True)
def seed_document_owner(db_session):
    db_session.add(
        User(
            id="user-1",
            email="metadata-user@example.com",
            display_name="Metadata User",
            status="active",
        )
    )
    db_session.commit()


def test_inline_text_processing_persists_success_metadata(db_session, monkeypatch):
    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "false")
    payload = b"# Metadata\nA successful parser records safe metadata."

    created = create_document_record(
        db_session,
        user_id="user-1",
        filename="metadata.md",
        mime_type="text/markdown",
        content_bytes=payload,
        processing_mode="inline",
    )

    stored = db_session.get(Document, created["id"])
    assert stored.size_bytes == len(payload)
    assert stored.parse_status == "success"
    assert stored.parse_error_code is None
    assert stored.page_count == 1
    assert stored.block_count == 1
    assert stored.parser_version == "document-parser-v3"
    assert stored.processing_started_at is not None
    assert stored.processing_completed_at is not None


def test_processing_marks_start_before_parser_and_records_stable_failure_code(
    db_session, monkeypatch
):
    import backend.app.application.document_service as document_service

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "false")

    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="failure.md",
        mime_type="text/markdown",
        content="content",
        processing_mode="defer",
    )
    observed: dict[str, object] = {}

    def fail_parser(*args, **kwargs):
        stored = db_session.get(Document, document["id"])
        observed["status"] = stored.parse_status
        observed["started_at"] = stored.processing_started_at
        observed["error_code"] = stored.parse_error_code
        raise ValueError("document parser produced no text")

    monkeypatch.setattr(document_service, "_parse_document_content", fail_parser)

    result = process_document_upload(
        db_session,
        document_id=document["id"],
        content_bytes=b"content",
    )

    stored = db_session.get(Document, document["id"])
    assert observed["status"] == "processing"
    assert observed["started_at"] is not None
    assert observed["error_code"] is None
    assert stored.processing_started_at is not None
    assert result["status"] == "failed"
    assert stored.parse_status == "failed"
    assert stored.parse_error_code == "document.parser_no_text"
    assert stored.processing_completed_at is not None


def test_recoverable_embedding_failure_returns_pending_then_exhausts_retries(
    db_session, monkeypatch
):
    import backend.app.application.document_service as document_service

    class FailingEmbeddingClient:
        def embed(self, text: str) -> list[float]:
            raise EmbeddingUnavailable("secret-provider-key is unavailable")

    monkeypatch.setattr(
        document_service, "build_embedding_client", lambda: FailingEmbeddingClient()
    )
    monkeypatch.setenv("DOCUMENT_PROCESSING_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("DOCUMENT_PROCESSING_RETRY_DELAY_SECONDS", "0")
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="retry.md",
        mime_type="text/markdown",
        content="retry content",
        processing_mode="defer",
    )
    event = db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.payload_json["document_id"].as_string() == document["id"]
        )
    )

    first = process_document_upload_event(db_session, event_id=event.id)
    pending = db_session.get(Document, document["id"])

    assert first["status"] == "pending"
    assert pending.parse_status == "pending"
    assert pending.parse_error_code == "document.embedding_unavailable"
    assert "secret-provider-key" not in pending.parse_error
    assert pending.processing_started_at is not None
    assert pending.processing_completed_at is None

    second = process_document_upload_event(db_session, event_id=event.id)
    failed = db_session.get(Document, document["id"])

    assert second["status"] == "failed"
    assert failed.parse_status == "failed"
    assert failed.parse_error_code == "document.processing_attempts_exhausted"
    assert "secret-provider-key" not in failed.parse_error
    assert failed.processing_completed_at is not None
