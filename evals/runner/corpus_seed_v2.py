"""Non-destructive seeding for the Chunk V2 evaluation index versions."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.persistence.repositories.document_index_repository import (
    deterministic_index_version_id,
)
from backend.app.models import Document, DocumentChunk, DocumentIndexVersion
from backend.app.services.embeddings import EmbeddingUnavailable
from evals.runner.corpus_seed import (
    CorpusSeedResult,
    _ensure_identity,
    _vector_literal,
    activate_versions,
    embedding_identity,
    retire_active_version,
)
from evals.runner.gold_chunk_map_v2 import (
    CHUNKING_V2_VERSION,
    build_corpus_chunks_v2,
    compute_chunking_v2_config_hash,
)


V2_CHUNK_SCHEMA_VERSION = "v2"


def seed_evaluation_corpus_v2(
    session: Session,
    *,
    corpus_dir: Path,
    embedding_client: object,
    reset: bool,
    namespace: str = "learning-qa-v1-chunking-v2",
) -> CorpusSeedResult:
    manifest, chunks_by_document = build_corpus_chunks_v2(corpus_dir)
    document_ids = [item.document_id for item in manifest.documents]
    build_key = f"evaluation-{CHUNKING_V2_VERSION}-{compute_chunking_v2_config_hash()}"
    version_ids = {
        document_id: deterministic_index_version_id(
            document_id=document_id,
            build_key=build_key,
        )
        for document_id in document_ids
    }
    expected_chunk_ids = {
        chunk.chunk_id
        for chunks in chunks_by_document.values()
        for chunk in chunks
    }

    if reset:
        session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id.in_(document_ids))
        )
        session.execute(delete(Document).where(Document.id.in_(document_ids)))
        session.flush()

    existing_documents = {
        document.id: document
        for document in session.scalars(
            select(Document).where(Document.id.in_(document_ids))
        ).all()
    }
    if existing_documents and len(existing_documents) != len(document_ids):
        raise ValueError(
            "evaluation corpus already exists with a different document shape; "
            "rerun with --reset"
        )

    stored_v2_chunks = session.scalars(
        select(DocumentChunk).where(DocumentChunk.id.in_(expected_chunk_ids))
    ).all()
    stored_v2_chunk_ids = {chunk.id for chunk in stored_v2_chunks}
    if stored_v2_chunk_ids and stored_v2_chunk_ids != expected_chunk_ids:
        raise ValueError(
            "evaluation Chunk V2 corpus already exists with a different shape; "
            "rerun with --reset"
        )
    if any(
        chunk.index_version_id != version_ids.get(chunk.document_id)
        for chunk in stored_v2_chunks
    ):
        raise ValueError(
            "evaluation Chunk V2 chunks reference an incompatible index version; "
            "rerun with --reset"
        )

    versions = {
        version.id: version
        for version in session.scalars(
            select(DocumentIndexVersion).where(
                DocumentIndexVersion.id.in_(set(version_ids.values()))
            )
        ).all()
    }
    if versions and (
        set(versions) != set(version_ids.values())
        or any(
            not _compatible_v2_version(version, build_key=build_key)
            for version in versions.values()
        )
    ):
        raise ValueError(
            "evaluation Chunk V2 index versions are incompatible; rerun with --reset"
        )
    if stored_v2_chunk_ids == expected_chunk_ids:
        if set(versions) != set(version_ids.values()):
            raise ValueError(
                "evaluation Chunk V2 index versions are missing; rerun with --reset"
            )
        activate_versions(session, versions.values())
        session.commit()
        return CorpusSeedResult(
            document_count=len(document_ids),
            chunk_count=len(expected_chunk_ids),
            reused=True,
        )
    if versions:
        raise ValueError(
            "evaluation Chunk V2 index versions have missing chunks; rerun with --reset"
        )

    _ensure_identity(session)
    manifest_by_id = {item.document_id: item for item in manifest.documents}
    if not existing_documents:
        for document_id in document_ids:
            item = manifest_by_id[document_id]
            session.add(
                Document(
                    id=document_id,
                    owner_user_id=None,
                    corpus_type="curated",
                    filename=item.filename,
                    object_key=f"evals/{namespace}/{item.filename}",
                    mime_type="text/markdown",
                    parse_status="success",
                    sha256=item.sha256,
                    source_url=f"eval://{namespace}/{document_id}",
                    trusted_level=3,
                    page_count=1,
                    block_count=len(chunks_by_document[document_id]),
                    parser_version="evaluation-chunking-v2",
                )
            )
        session.flush()

    embedding_model, embedding_dimensions = embedding_identity(embedding_client)
    now = datetime.now(timezone.utc)
    for document_id in document_ids:
        retire_active_version(session, document_id=document_id)
        version = DocumentIndexVersion(
            id=version_ids[document_id],
            document_id=document_id,
            build_key=build_key,
            status="active",
            chunk_schema_version=V2_CHUNK_SCHEMA_VERSION,
            chunker_version=CHUNKING_V2_VERSION,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            build_attempt=1,
            chunk_count=len(chunks_by_document[document_id]),
            completed_at=now,
            activated_at=now,
        )
        session.add(version)
        session.flush([version])

    all_chunks = [
        chunk
        for document_id in document_ids
        for chunk in chunks_by_document[document_id]
    ]
    vectors = _embed_chunks(embedding_client, [chunk.content for chunk in all_chunks])
    if len(vectors) != len(all_chunks):
        raise EmbeddingUnavailable(
            f"embedding provider returned {len(vectors)} vectors for {len(all_chunks)} chunks"
        )
    for chunk, values in zip(all_chunks, vectors):
        vector = [float(value) for value in values]
        if len(vector) != embedding_dimensions:
            raise EmbeddingUnavailable(
                f"expected {embedding_dimensions}-dimensional embedding, got {len(vector)}"
            )
        item = manifest_by_id[chunk.document_id]
        metadata = {
            **dict(chunk.metadata),
            "index_version_id": version_ids[chunk.document_id],
            "evaluation_namespace": namespace,
            "untrusted_input": False,
        }
        session.add(
            DocumentChunk(
                id=chunk.chunk_id,
                document_id=chunk.document_id,
                index_version_id=version_ids[chunk.document_id],
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                token_count=len(chunk.content.split()),
                embedding=vector,
                embedding_vector=_vector_literal(vector),
                metadata_json=metadata,
                citation_label=f"{item.title} · chunk {chunk.chunk_index}",
            )
        )
    session.commit()
    return CorpusSeedResult(
        document_count=len(document_ids),
        chunk_count=len(all_chunks),
        reused=False,
    )


def _compatible_v2_version(
    version: DocumentIndexVersion,
    *,
    build_key: str,
) -> bool:
    return (
        version.build_key == build_key
        and version.chunk_schema_version == V2_CHUNK_SCHEMA_VERSION
        and version.chunker_version == CHUNKING_V2_VERSION
    )


def _embed_chunks(client: object, contents: list[str]) -> list[list[float]]:
    embed_batch = getattr(client, "embed_batch", None)
    if callable(embed_batch):
        return list(embed_batch(contents))
    embed = getattr(client, "embed", None)
    if not callable(embed):
        raise EmbeddingUnavailable("embedding client must provide embed_batch or embed")
    return [list(embed(content)) for content in contents]
