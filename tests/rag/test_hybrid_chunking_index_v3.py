from __future__ import annotations

from backend.app.domain.rag.chunking import ChunkDraft, ChunkMetadataBuilder, ChunkType, DEFAULT_CHUNK_POLICY


class RecordingEmbeddingClient:
    provider_identity = "test-v3-provider"
    model = "test-v3-model"
    dimensions = 3

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


def _document(session):
    from backend.app.models import Document

    document = Document(
        id="doc-v3",
        owner_user_id=None,
        corpus_type="curated",
        filename="lesson.pdf",
        object_key="evals/lesson.pdf",
        mime_type="application/pdf",
        parse_status="success",
        sha256="b" * 64,
        trusted_level=3,
    )
    session.add(document)
    session.flush()
    return document


def test_index_service_preserves_v3_schema_and_exact_final_token_count(db_session) -> None:
    from backend.app.application.document_index_service import DocumentIndexService
    from sqlalchemy import select
    from backend.app.models import DocumentChunk

    document = _document(db_session)
    chunks = [{
        "content": "最终渲染后的中文 chunk",
        "metadata": {
            "chunk_schema_version": "v3",
            "token_count": 17,
            "page_start": 3,
            "page_end": 4,
            "chunk_type": "text",
        },
    }]

    version = DocumentIndexService(db_session, RecordingEmbeddingClient()).build_index(
        user_id=None,
        document_id=document.id,
        build_key="v3-build",
        chunks=chunks,
        chunker_version="document-parser-v4:hybrid-chunking-v3:abc123",
        chunk_schema_version="v3",
    )
    stored = db_session.scalar(select(DocumentChunk).where(DocumentChunk.index_version_id == version.id))

    assert version.chunk_schema_version == "v3"
    assert stored.metadata_json["chunk_schema_version"] == "v3"
    assert stored.token_count == 17
    assert stored.citation_label == "lesson.pdf · pages 3–4 · block 1 · chunk 1"


def test_v2_index_service_keeps_split_token_fallback_and_page_label(db_session) -> None:
    from backend.app.application.document_index_service import DocumentIndexService
    from sqlalchemy import select
    from backend.app.models import DocumentChunk

    document = _document(db_session)
    version = DocumentIndexService(db_session, RecordingEmbeddingClient()).build_index(
        user_id=None,
        document_id=document.id,
        build_key="v2-build",
        chunks=[{"content": "one two three", "metadata": {"page_number": 2}}],
        chunker_version="document-parser-v3:chunking-v2",
    )
    stored = db_session.scalar(select(DocumentChunk).where(DocumentChunk.index_version_id == version.id))

    assert version.chunk_schema_version == "v2"
    assert stored.token_count == 3
    assert stored.citation_label == "lesson.pdf · page 2 · block 1 · chunk 1"
