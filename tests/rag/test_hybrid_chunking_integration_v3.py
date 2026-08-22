from __future__ import annotations

import pytest


def test_v2_strategy_keeps_existing_markdown_payload_shape(monkeypatch) -> None:
    from backend.app.application.document_chunking_service import DocumentChunkingService
    from backend.app.domain.rag.chunking.v3.config import ChunkingStrategy

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "false")
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
    assert result.parser_version == "text-parser-v1"
    assert result.chunks
    for chunk in result.chunks:
        metadata = chunk["metadata"]
        assert metadata["chunk_schema_version"] == "v3"
        assert metadata["source_type"] == "markdown"
        assert metadata["policy_fingerprint"] == result.execution_config.policy_fingerprint
        assert metadata["tokenizer_id"] == "cl100k_base"
        assert metadata["size_guard"]["token_count"] == TiktokenTokenCounter().count(chunk["content"])


def test_v3_binary_chunking_passes_supplied_vision_client_to_parser(monkeypatch) -> None:
    import backend.app.application.document_chunking_service as chunking_service
    from backend.app.domain.rag.chunking.v3.config import DocumentParsingProfile
    from backend.app.services.document_parsing.models import (
        DocumentBlock,
        DocumentBlockType,
        DocumentFileType,
        DocumentParseResult,
        ParseStatus,
        ProcessingMode,
        SourceElementType,
    )

    vision_client = object()
    received: list[tuple[object, DocumentParsingProfile]] = []

    class RecordingParser:
        def __init__(self, *, ocr_service=None, vision_client=None) -> None:
            assert ocr_service is None
            self.vision_client = vision_client

        async def parse_document(self, *, content, filename, mime_type, profile):
            received.append((self.vision_client, profile))
            return DocumentParseResult(
                status=ParseStatus.SUCCESS,
                filename=filename,
                file_type=DocumentFileType.IMAGE,
                mime_type=mime_type,
                content_sha256="a" * 64,
                parser_version="document-parser-v4.1",
                page_count=1,
                block_count=1,
                blocks=[
                    DocumentBlock(
                        filename=filename,
                        file_type=DocumentFileType.IMAGE,
                        page_number=1,
                        block_index=1,
                        text="Owner-selected vision content",
                        processing_mode=ProcessingMode.IMAGE_OCR,
                        source_element=SourceElementType.IMAGE_FILE,
                        block_type=DocumentBlockType.IMAGE_DESCRIPTION,
                    )
                ],
                processing_time_ms=0,
            )

    class DeterministicBatchEmbedding:
        def embed_batch(self, texts):
            return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setattr(chunking_service, "DocumentParser", RecordingParser)
    service = chunking_service.DocumentChunkingService.from_environment(
        embedding_client=DeterministicBatchEmbedding()
    )

    result = service.chunk_upload(
        b"image bytes are parsed by the recording parser",
        filename="owner.png",
        mime_type="image/png",
        document_id="doc-owner-vision",
        vision_client=vision_client,
    )

    assert result.parser_version == "document-parser-v4.1"
    assert received == [(vision_client, DocumentParsingProfile.STRUCTURED_V3)]
    assert result.chunks[0]["metadata"]["source_type"] == "uploaded_document"
    assert result.chunks[0]["metadata"]["processing_source_type"] == "image_ocr"


def test_v3_plain_text_metadata_keeps_legacy_source_type_alias(monkeypatch) -> None:
    from backend.app.application.document_chunking_service import DocumentChunkingService

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setenv("EMBEDDING_BACKEND", "deterministic")

    result = DocumentChunkingService.from_environment().chunk_text(
        b"Plain text remains compatible.",
        filename="notes.txt",
        mime_type="text/plain",
        document_id="doc-v3-text",
    )

    assert result.chunks[0]["metadata"]["source_type"] == "text"


@pytest.mark.parametrize(
    ("file_type", "processing_mode", "source_element", "expected_processing_source_type"),
    [
        ("pdf", "pdf_text", "pdf_text_layer", "pdf"),
        ("pptx", "ppt_native_text", "ppt_text_shapes", "pptx"),
    ],
)
def test_v3_binary_metadata_keeps_legacy_source_aliases(
    monkeypatch,
    file_type,
    processing_mode,
    source_element,
    expected_processing_source_type,
) -> None:
    from backend.app.application.document_chunking_service import DocumentChunkingService
    from backend.app.services.document_parsing.models import (
        DocumentBlock,
        DocumentBlockType,
        DocumentFileType,
        DocumentParseResult,
        ParseStatus,
        ProcessingMode,
        SourceElementType,
    )

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setenv("EMBEDDING_BACKEND", "deterministic")
    parsed = DocumentParseResult(
        status=ParseStatus.SUCCESS,
        filename=f"lesson.{file_type}",
        file_type=DocumentFileType(file_type),
        mime_type="application/octet-stream",
        content_sha256="b" * 64,
        parser_version="document-parser-v4.1",
        page_count=1,
        block_count=1,
        blocks=[
            DocumentBlock(
                filename=f"lesson.{file_type}",
                file_type=DocumentFileType(file_type),
                page_number=1,
                block_index=1,
                text="Structured binary document content",
                processing_mode=ProcessingMode(processing_mode),
                source_element=SourceElementType(source_element),
                block_type=DocumentBlockType.PARAGRAPH,
            )
        ],
        processing_time_ms=0,
    )

    result = DocumentChunkingService.from_environment().chunk_parsed_document(
        parsed,
        document_id=f"doc-v3-{file_type}",
    )

    metadata = result.chunks[0]["metadata"]
    assert metadata["source_type"] == "uploaded_document"
    assert metadata["processing_source_type"] == expected_processing_source_type


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


def test_v3_structured_parser_failure_is_permanent_and_typed(monkeypatch) -> None:
    from backend.app.application.document_chunking_service import DocumentChunkingService
    from backend.app.domain.rag.chunking.v3.errors import StructuredParsingError

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    service = DocumentChunkingService.from_environment()

    with pytest.raises(StructuredParsingError) as exc_info:
        service.chunk_upload(
            b"\xff\xfe",
            filename="broken.md",
            mime_type="text/markdown",
            document_id="doc-v3-parser-failure",
        )

    assert exc_info.value.retryable is False


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
