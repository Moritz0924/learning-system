from __future__ import annotations

import pytest
from dataclasses import replace


def test_execution_snapshot_is_versioned_and_v2_never_carries_v3_policy(monkeypatch) -> None:
    from backend.app.application.document_chunking_service import resolve_chunking_execution_snapshot
    from backend.app.domain.rag.chunking.v3.config import ChunkingStrategy

    monkeypatch.delenv("FEATURE_HYBRID_CHUNKING_V3", raising=False)

    snapshot = resolve_chunking_execution_snapshot(
        filename="notes.txt",
        mime_type="text/plain",
    )

    assert snapshot.snapshot_version == "chunking-execution-v1"
    assert snapshot.strategy is ChunkingStrategy.V2
    assert snapshot.parser_profile.value == "legacy_v2"
    assert snapshot.parser_implementation_version == "legacy-parser-v3"
    assert snapshot.chunking_implementation_version == "chunking-v2"
    assert snapshot.v3_policy is None
    assert snapshot.policy_fingerprint is None
    assert snapshot.tokenizer_id is None


def test_execution_snapshot_resolver_is_pure_configuration(monkeypatch) -> None:
    import backend.app.application.document_chunking_service as chunking_service

    monkeypatch.setattr(
        chunking_service,
        "build_embedding_client",
        lambda: (_ for _ in ()).throw(AssertionError("must not construct embedding client")),
    )
    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")

    snapshot = chunking_service.resolve_chunking_execution_snapshot(
        filename="guide.md",
        mime_type="text/markdown",
    )

    assert snapshot.strategy.value == "hybrid_v3"
    assert snapshot.v3_policy is not None
    assert snapshot.policy_fingerprint
    assert snapshot.tokenizer_id == "cl100k_base"


def test_snapshot_restore_ignores_current_feature_flag_and_rejects_old_v3_payload(monkeypatch) -> None:
    from backend.app.application.document_chunking_service import (
        DocumentChunkingService,
        resolve_chunking_execution_snapshot,
    )
    from backend.app.domain.rag.chunking.v3.errors import HybridChunkingSnapshotIncompatible

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    v3_snapshot = resolve_chunking_execution_snapshot(filename="guide.md", mime_type="text/markdown")
    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "false")

    restored = DocumentChunkingService.from_execution_snapshot(v3_snapshot)

    assert restored.strategy.value == "hybrid_v3"
    with pytest.raises(HybridChunkingSnapshotIncompatible):
        DocumentChunkingService.from_execution_snapshot(
            replace(v3_snapshot, parser_implementation_version="document-parser-v999"),
        )
    with pytest.raises(HybridChunkingSnapshotIncompatible):
        DocumentChunkingService.from_execution_snapshot(
            {
                "strategy": "hybrid_v3",
                "parser_profile": "structured_v3",
                "policy": {},
                "policy_fingerprint": "old",
                "tokenizer_id": "cl100k_base",
            }
        )


def test_chunk_upload_routes_v3_markdown_and_text_to_text_native_parser(monkeypatch) -> None:
    from backend.app.application.document_chunking_service import DocumentChunkingService
    from backend.app.domain.rag.chunking.v3.config import ChunkingStrategy

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setenv("EMBEDDING_BACKEND", "deterministic")
    service = DocumentChunkingService.from_environment()

    for filename, mime_type, content, source_format in (
        ("guide.md", "text/markdown", b"# Guide\n\nNative text route.", "markdown"),
        ("notes.txt", "text/plain", b"Native text route.", "plain_text"),
    ):
        result = service.chunk_upload(
            content,
            filename=filename,
            mime_type=mime_type,
            document_id=f"doc-{filename}",
        )

        assert result.strategy is ChunkingStrategy.HYBRID_V3
        assert result.parser_version == "text-parser-v1"
        assert result.chunks
        assert all(chunk["metadata"]["source_location_kind"] == "text" for chunk in result.chunks)
        assert all(chunk["metadata"]["page_start"] is None for chunk in result.chunks)
        assert all(chunk["metadata"]["source_spans"][0]["source_locator"].startswith(f"doc-{filename}:text:") for chunk in result.chunks)
        assert all(chunk["metadata"]["file_type"] == "text" for chunk in result.chunks)
        assert all(chunk["metadata"]["source_format"] == source_format for chunk in result.chunks)
        assert all(chunk["metadata"]["source_provenance"] == {
            "file_type": ["text"],
            "processing_mode": ["text_native"],
            "source_element": ["text_file"],
            "source_format": [source_format],
        } for chunk in result.chunks)
