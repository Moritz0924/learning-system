from __future__ import annotations

from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.document_index_service import embedding_client_identity
from backend.app.models import DocumentChunk, DocumentIndexVersion
from evals.models import CorpusManifest
from evals.runner.corpus_seed import legacy_index_version_id
from evals.runner.gold_chunk_map import build_corpus_chunks
from evals.runner.gold_chunk_map_v2 import (
    EVALUATION_V2_CHUNKER_VERSION,
    build_corpus_chunks_v2,
    evaluation_v2_index_build_key,
    evaluation_v2_index_version_id,
)


EvaluationIndexSchema = Literal["legacy-v1", "v2"]


def validate_evaluation_index_schema(
    session: Session,
    *,
    corpus_dir: Path,
    index_schema: EvaluationIndexSchema,
    embedding_client: object,
) -> None:
    manifest, expected_chunks, expected_versions = _expected_profile(
        corpus_dir,
        index_schema=index_schema,
        embedding_client=embedding_client,
    )
    document_ids = [item.document_id for item in manifest.documents]
    active_versions = {
        version.document_id: version
        for version in session.scalars(
            select(DocumentIndexVersion).where(
                DocumentIndexVersion.document_id.in_(document_ids),
                DocumentIndexVersion.status == "active",
            )
        ).all()
    }
    reasons: list[str] = []
    if set(active_versions) != set(document_ids):
        reasons.append("active versions do not cover the full evaluation corpus")
    for document_id in document_ids:
        version = active_versions.get(document_id)
        expected = expected_versions[document_id]
        if version is None:
            continue
        if version.id != expected["id"]:
            reasons.append(f"{document_id} active version id")
        if version.build_key != expected["build_key"]:
            reasons.append(f"{document_id} build key")
        if version.chunk_schema_version != expected["chunk_schema_version"]:
            reasons.append(f"{document_id} chunk schema")
        if version.chunker_version != expected["chunker_version"]:
            reasons.append(f"{document_id} chunker version")
        if version.embedding_provider != expected["embedding_provider"]:
            reasons.append(f"{document_id} embedding provider")
        actual_chunk_ids = set(
            session.scalars(
                select(DocumentChunk.id).where(
                    DocumentChunk.index_version_id == version.id
                )
            )
        )
        if actual_chunk_ids != expected_chunks[document_id]:
            reasons.append(f"{document_id} chunk ids")
    if reasons:
        detail = ", ".join(sorted(set(reasons)))
        raise ValueError(
            f"active evaluation index mismatch for {index_schema}: {detail}"
        )


def _expected_profile(
    corpus_dir: Path,
    *,
    index_schema: EvaluationIndexSchema,
    embedding_client: object,
) -> tuple[
    CorpusManifest,
    dict[str, set[str]],
    dict[str, dict[str, str]],
]:
    provider, _, _ = embedding_client_identity(embedding_client)
    if index_schema == "legacy-v1":
        manifest, chunks_by_document = build_corpus_chunks(corpus_dir)
        expected_versions = {
            item.document_id: {
                "id": legacy_index_version_id(item.document_id),
                "build_key": "legacy-v1",
                "chunk_schema_version": "legacy-v1",
                "chunker_version": "legacy-split-text-v1",
                "embedding_provider": "legacy-unknown",
            }
            for item in manifest.documents
        }
    elif index_schema == "v2":
        manifest, chunks_by_document = build_corpus_chunks_v2(
            corpus_dir,
            embedding_client=embedding_client,
        )
        expected_versions = {
            item.document_id: {
                "id": evaluation_v2_index_version_id(
                    item,
                    embedding_client=embedding_client,
                ),
                "build_key": evaluation_v2_index_build_key(
                    item,
                    embedding_client=embedding_client,
                ),
                "chunk_schema_version": "v2",
                "chunker_version": EVALUATION_V2_CHUNKER_VERSION,
                "embedding_provider": provider,
            }
            for item in manifest.documents
        }
    else:
        raise ValueError(f"unsupported evaluation index schema: {index_schema}")
    expected_chunks = {
        document_id: {chunk.chunk_id for chunk in chunks}
        for document_id, chunks in chunks_by_document.items()
    }
    return manifest, expected_chunks, expected_versions
