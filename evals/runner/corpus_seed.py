"""Idempotent seeding of the fixed V1 evaluation corpus."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.app.models import (
    Document,
    DocumentChunk,
    DocumentIndexVersion,
    LearningGoal,
    User,
)
from backend.app.services.embeddings import EmbeddingUnavailable
from evals.runner.gold_chunk_map import build_corpus_chunks


EVALUATION_USER_ID = "eval-user-learning-qa-v1"
EVALUATION_GOAL_ID = "eval-goal-learning-qa-v1"
LEGACY_INDEX_BUILD_KEY = "legacy-v1"
LEGACY_CHUNK_SCHEMA_VERSION = "legacy-v1"
LEGACY_CHUNKER_VERSION = "legacy-split-text-v1"


@dataclass(frozen=True)
class CorpusSeedResult:
    document_count: int
    chunk_count: int
    reused: bool


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def legacy_index_version_id(document_id: str) -> str:
    """Return the same legacy identity used by migration 20260729_0017."""
    identity = f"document-index-legacy-v1\0{document_id}".encode("utf-8")
    return f"index-{sha256(identity).hexdigest()[:32]}"


def _ensure_identity(session: Session) -> None:
    if session.get(User, EVALUATION_USER_ID) is None:
        session.add(
            User(
                id=EVALUATION_USER_ID,
                email="learning-qa-v1@evaluation.invalid",
                normalized_email="learning-qa-v1@evaluation.invalid",
                display_name="LLM RAG Evaluation",
                status="active",
            )
        )
        session.flush()
    if session.get(LearningGoal, EVALUATION_GOAL_ID) is None:
        session.add(
            LearningGoal(
                id=EVALUATION_GOAL_ID,
                user_id=EVALUATION_USER_ID,
                title="Learning QA Evaluation",
                domain="evaluation",
                target_outcome="Evaluate offline LLM and RAG behavior",
                weekly_hours_target=1,
                status="active",
            )
        )
        session.flush()


def seed_evaluation_corpus(
    session: Session,
    *,
    corpus_dir: Path,
    embedding_client: object,
    reset: bool,
    namespace: str = "learning-qa-v1",
) -> CorpusSeedResult:
    """Seed the byte-for-byte compatible legacy V1 chunks under active V1 indexes."""
    manifest, chunks_by_document = build_corpus_chunks(corpus_dir)
    document_ids = [item.document_id for item in manifest.documents]
    version_ids = {
        document_id: legacy_index_version_id(document_id)
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

    existing = {
        document.id: document
        for document in session.scalars(
            select(Document).where(Document.id.in_(document_ids))
        ).all()
    }
    current_chunks = session.scalars(
        select(DocumentChunk).where(DocumentChunk.id.in_(expected_chunk_ids))
    ).all()
    current_chunk_ids = {chunk.id for chunk in current_chunks}
    versions = {
        version.id: version
        for version in session.scalars(
            select(DocumentIndexVersion).where(
                DocumentIndexVersion.id.in_(set(version_ids.values()))
            )
        ).all()
    }

    if current_chunk_ids and current_chunk_ids != expected_chunk_ids:
        raise ValueError(
            "evaluation corpus legacy-v1 chunks already exist with a different shape; "
            "rerun with --reset"
        )
    if any(
        chunk.index_version_id != version_ids.get(chunk.document_id)
        for chunk in current_chunks
    ):
        raise ValueError(
            "evaluation corpus legacy-v1 chunks reference an incompatible index version; "
            "rerun with --reset"
        )
    if versions and (
        set(versions) != set(version_ids.values())
        or any(not _compatible_legacy_version(version) for version in versions.values())
    ):
        raise ValueError(
            "evaluation corpus legacy-v1 indexes are incompatible; rerun with --reset"
        )
    if len(existing) == len(document_ids) and current_chunk_ids == expected_chunk_ids:
        if set(versions) != set(version_ids.values()):
            raise ValueError(
                "evaluation corpus legacy-v1 index versions are missing; rerun with --reset"
            )
        _activate_versions(session, versions.values())
        session.commit()
        return CorpusSeedResult(
            document_count=len(document_ids),
            chunk_count=len(expected_chunk_ids),
            reused=True,
        )
    if existing:
        raise ValueError(
            "evaluation corpus already exists with a different shape; rerun with --reset"
        )

    _ensure_identity(session)
    manifest_by_id = {item.document_id: item for item in manifest.documents}
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
                parser_version="evaluation-split-text-v1",
            )
        )
    session.flush()

    embedding_model, embedding_dimensions = embedding_identity(embedding_client)
    now = datetime.now(timezone.utc)
    for document_id in document_ids:
        version = DocumentIndexVersion(
            id=version_ids[document_id],
            document_id=document_id,
            build_key=LEGACY_INDEX_BUILD_KEY,
            status="active",
            chunk_schema_version=LEGACY_CHUNK_SCHEMA_VERSION,
            chunker_version=LEGACY_CHUNKER_VERSION,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            build_attempt=1,
            chunk_count=len(chunks_by_document[document_id]),
            completed_at=now,
            activated_at=now,
        )
        session.add(version)
    session.flush()

    total_chunks = 0
    for document_id in document_ids:
        item = manifest_by_id[document_id]
        for chunk in chunks_by_document[document_id]:
            values = embedding_client.embed(chunk.content)
            if len(values) != embedding_dimensions:
                raise EmbeddingUnavailable(
                    f"expected {embedding_dimensions}-dimensional embedding, got {len(values)}"
                )
            session.add(
                DocumentChunk(
                    id=chunk.chunk_id,
                    document_id=document_id,
                    index_version_id=version_ids[document_id],
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    token_count=len(chunk.content.split()),
                    embedding=values,
                    embedding_vector=_vector_literal(values),
                    metadata_json={
                        "source_type": "markdown",
                        "chunk_index": chunk.chunk_index,
                        "evaluation_namespace": namespace,
                        "untrusted_input": False,
                    },
                    citation_label=f"{item.title} · chunk {chunk.chunk_index}",
                )
            )
            total_chunks += 1
    session.commit()
    return CorpusSeedResult(
        document_count=len(document_ids),
        chunk_count=total_chunks,
        reused=False,
    )


def embedding_identity(embedding_client: object) -> tuple[str, int]:
    model = str(
        getattr(embedding_client, "model", None)
        or getattr(embedding_client, "mode", None)
        or "unknown-embedding-model"
    )
    try:
        dimensions = int(getattr(embedding_client, "dimensions", 1536))
    except (TypeError, ValueError) as exc:
        raise EmbeddingUnavailable("embedding dimensions must be a positive integer") from exc
    if dimensions <= 0:
        raise EmbeddingUnavailable("embedding dimensions must be a positive integer")
    return model, dimensions


def _compatible_legacy_version(version: DocumentIndexVersion) -> bool:
    return (
        version.build_key == LEGACY_INDEX_BUILD_KEY
        and version.chunk_schema_version == LEGACY_CHUNK_SCHEMA_VERSION
        and version.chunker_version == LEGACY_CHUNKER_VERSION
    )


def retire_active_version(
    session: Session,
    *,
    document_id: str,
    except_version_id: str | None = None,
) -> None:
    conditions = [
        DocumentIndexVersion.document_id == document_id,
        DocumentIndexVersion.status == "active",
    ]
    if except_version_id is not None:
        conditions.append(DocumentIndexVersion.id != except_version_id)
    session.execute(
        update(DocumentIndexVersion)
        .where(*conditions)
        .values(status="retired", retired_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session="fetch")
    )
    session.flush()


def activate_versions(
    session: Session,
    versions: Iterable[DocumentIndexVersion],
) -> None:
    _activate_versions(session, versions)


def _activate_versions(
    session: Session,
    versions: Iterable[DocumentIndexVersion],
) -> None:
    now = datetime.now(timezone.utc)
    for version in versions:
        if version.status == "active":
            continue
        if version.status not in {"ready", "retired"}:
            raise ValueError(
                f"evaluation index {version.id} cannot be activated from {version.status}"
            )
        retire_active_version(
            session,
            document_id=version.document_id,
            except_version_id=version.id,
        )
        version.status = "active"
        version.activated_at = now
        version.retired_at = None
        session.flush([version])
