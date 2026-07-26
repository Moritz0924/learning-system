"""Idempotent seeding of the fixed evaluation corpus into an injected database session."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import Document, DocumentChunk, LearningGoal, User
from backend.app.services.embeddings import EmbeddingUnavailable
from evals.runner.gold_chunk_map import build_corpus_chunks


EVALUATION_USER_ID = "eval-user-learning-qa-v1"
EVALUATION_GOAL_ID = "eval-goal-learning-qa-v1"


@dataclass(frozen=True)
class CorpusSeedResult:
    document_count: int
    chunk_count: int
    reused: bool


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


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
    manifest, chunks_by_document = build_corpus_chunks(corpus_dir)
    document_ids = [item.document_id for item in manifest.documents]

    if reset:
        session.execute(delete(DocumentChunk).where(DocumentChunk.document_id.in_(document_ids)))
        session.execute(delete(Document).where(Document.id.in_(document_ids)))
        session.flush()

    existing = {
        document.id: document
        for document in session.scalars(select(Document).where(Document.id.in_(document_ids))).all()
    }
    expected_chunk_ids = {chunk.chunk_id for chunks in chunks_by_document.values() for chunk in chunks}
    current_chunk_ids = set(
        session.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id.in_(document_ids))).all()
    )
    if len(existing) == len(document_ids) and current_chunk_ids == expected_chunk_ids:
        return CorpusSeedResult(
            document_count=len(document_ids),
            chunk_count=len(expected_chunk_ids),
            reused=True,
        )
    if existing:
        raise ValueError("evaluation corpus already exists with a different shape; rerun with --reset")

    _ensure_identity(session)
    manifest_by_id = {item.document_id: item for item in manifest.documents}
    total_chunks = 0
    for document_id in document_ids:
        item = manifest_by_id[document_id]
        document = Document(
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
        session.add(document)
        for chunk in chunks_by_document[document_id]:
            values = embedding_client.embed(chunk.content)
            if len(values) != 1536:
                raise EmbeddingUnavailable(
                    f"expected 1536-dimensional embedding, got {len(values)}"
                )
            session.add(
                DocumentChunk(
                    id=chunk.chunk_id,
                    document_id=document_id,
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
    return CorpusSeedResult(document_count=len(document_ids), chunk_count=total_chunks, reused=False)
