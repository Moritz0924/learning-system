from __future__ import annotations

import pytest


def test_v2_strategy_keeps_existing_markdown_payload_shape(monkeypatch) -> None:
    from backend.app.application.document_chunking_service import DocumentChunkingService
    from backend.app.domain.rag.chunking.v3.config import ChunkingStrategy

    monkeypatch.delenv("FEATURE_HYBRID_CHUNKING_V3", raising=False)
    result = DocumentChunkingService.from_environment().chunk_text(
        b"# Guide\nLegacy chunking remains the default.",
        filename="guide.md",
        mime_type="text/markdown",
        document_id="doc-v2",
    )

    assert result.strategy is ChunkingStrategy.V2
    assert result.chunks[0]["metadata"]["chunk_schema_version"] == "v2"
    assert result.chunks[0]["content"] == "# Guide\nLegacy chunking remains the default."


def test_v3_strategy_generates_structured_metadata_and_exact_token_count(monkeypatch) -> None:
    from backend.app.application.document_chunking_service import DocumentChunkingService
    from backend.app.domain.rag.chunking.v3.config import ChunkingStrategy
    from backend.app.services.token_counting import TiktokenTokenCounter

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setenv("EMBEDDING_BACKEND", "deterministic")
    result = DocumentChunkingService.from_environment().chunk_text(
        b"# Guide\n\nRetrieval keeps related evidence together.\n\nA second paragraph continues the topic.",
        filename="guide.md",
        mime_type="text/markdown",
        document_id="doc-v3",
    )

    assert result.strategy is ChunkingStrategy.HYBRID_V3
    assert result.parser_version == "document-parser-v4.1"
    assert result.chunks
    for chunk in result.chunks:
        metadata = chunk["metadata"]
        assert metadata["chunk_schema_version"] == "v3"
        assert metadata["policy_fingerprint"] == result.execution_config.policy_fingerprint
        assert metadata["tokenizer_id"] == "cl100k_base"
        assert metadata["size_guard"]["token_count"] == TiktokenTokenCounter().count(chunk["content"])


def test_v3_embedding_failure_is_visible_and_never_routes_to_v2(monkeypatch) -> None:
    from backend.app.application.document_chunking_service import DocumentChunkingService
    from backend.app.domain.rag.chunking.v3.errors import SemanticEmbeddingUnavailable

    class FailingEncoder:
        def embed_batch(self, texts):
            raise SemanticEmbeddingUnavailable("temporary embedding failure")

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    service = DocumentChunkingService.from_environment(embedding_client=FailingEncoder())

    with pytest.raises(SemanticEmbeddingUnavailable):
        service.chunk_text(
            b"# Guide\n\nText that requires semantic analysis.\n\nA second semantic paragraph.",
            filename="guide.md",
            mime_type="text/markdown",
            document_id="doc-v3-failure",
        )


def test_outbox_snapshot_is_written_only_for_v3(monkeypatch, db_session) -> None:
    from backend.app.application.document_service import create_document_record
    from backend.app.models import OutboxEvent
    from sqlalchemy import select
    from backend.app.models import User

    db_session.add(User(
        id="snapshot-user",
        email="snapshot-user@example.test",
        display_name="Snapshot User",
        status="active",
    ))
    db_session.flush()

    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "defer")
    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    create_document_record(
        db_session,
        user_id="snapshot-user",
        filename="snapshot.md",
        mime_type="text/markdown",
        content="# Snapshot\nFrozen strategy.",
        processing_mode="defer",
    )
    event = db_session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "document.process_upload"))

    assert event.payload_json["chunking_execution"]["strategy"] == "hybrid_v3"
    assert event.payload_json["chunking_execution"]["parser_profile"] == "structured_v3"
    assert event.payload_json["chunking_execution"]["policy_fingerprint"]


def test_execution_snapshot_keeps_policy_when_environment_changes(monkeypatch) -> None:
    from backend.app.application.document_chunking_service import DocumentChunkingService
    from backend.app.domain.rag.chunking.v3.config import HybridChunkPolicy

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setenv("HYBRID_CHUNK_MAX_TOKENS", "512")
    original = DocumentChunkingService.from_environment()
    payload = original.execution_config.to_payload()
    monkeypatch.setenv("HYBRID_CHUNK_MAX_TOKENS", "128")
    restored = DocumentChunkingService.from_execution_config(payload)

    assert restored.execution_config.v3_policy.size.max_tokens == 512
    assert restored.execution_config.policy_fingerprint == original.execution_config.policy_fingerprint
