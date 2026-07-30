from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.db import Base, enable_sqlite_foreign_keys
from backend.app.models import Document, DocumentChunk, DocumentIndexVersion
from backend.app.services.embeddings import DeterministicEmbeddingClient


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "evals" / "corpus" / "learning_qa_v1"


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    return Session(engine)


def _embedding_client(
    provider: str,
    *,
    model: str = "deterministic-model-a",
    dimensions: int = 3,
) -> DeterministicEmbeddingClient:
    client = DeterministicEmbeddingClient()
    client.provider_identity = provider
    client.model = model
    client.dimensions = dimensions
    return client


def test_seed_is_idempotent_and_uses_deterministic_full_corpus_chunks() -> None:
    from evals.runner.corpus_seed import seed_evaluation_corpus
    from evals.runner.gold_chunk_map import build_corpus_chunks

    session = _session()
    client = DeterministicEmbeddingClient()
    first = seed_evaluation_corpus(session, corpus_dir=CORPUS, embedding_client=client, reset=False)
    second = seed_evaluation_corpus(session, corpus_dir=CORPUS, embedding_client=client, reset=False)
    _, expected = build_corpus_chunks(CORPUS)

    documents = session.scalars(select(Document).order_by(Document.id)).all()
    chunks = session.scalars(select(DocumentChunk).order_by(DocumentChunk.id)).all()
    versions = session.scalars(
        select(DocumentIndexVersion).order_by(DocumentIndexVersion.document_id)
    ).all()
    expected_ids = {chunk.chunk_id for values in expected.values() for chunk in values}

    assert first.document_count == second.document_count == 5
    assert len(documents) == 5
    assert {chunk.id for chunk in chunks} == expected_ids
    assert all(document.corpus_type == "curated" for document in documents)
    assert all(chunk.metadata_json["evaluation_namespace"] == "learning-qa-v1" for chunk in chunks)
    assert len(versions) == 5
    assert all(version.status == "active" for version in versions)
    assert all(version.chunk_schema_version == "legacy-v1" for version in versions)
    assert all(version.chunker_version == "legacy-split-text-v1" for version in versions)
    assert all(
        version.embedding_provider == client.provider_identity
        for version in versions
    )
    assert all(version.embedding_model == client.model for version in versions)
    assert all(version.embedding_dimensions == client.dimensions for version in versions)
    version_by_document = {version.document_id: version.id for version in versions}
    assert all(chunk.index_version_id == version_by_document[chunk.document_id] for chunk in chunks)


def test_legacy_seed_rejects_reuse_with_a_different_embedding_provider() -> None:
    import pytest

    from evals.runner.corpus_seed import seed_evaluation_corpus

    session = _session()
    seed_evaluation_corpus(
        session,
        corpus_dir=CORPUS,
        embedding_client=_embedding_client("provider-a"),
        reset=False,
    )

    with pytest.raises(ValueError, match="legacy-v1 indexes are incompatible.*--reset"):
        seed_evaluation_corpus(
            session,
            corpus_dir=CORPUS,
            embedding_client=_embedding_client("provider-b"),
            reset=False,
        )


def test_reset_deletes_only_manifest_documents() -> None:
    from evals.runner.corpus_seed import seed_evaluation_corpus

    session = _session()
    session.add(
        Document(
            id="unrelated-doc",
            owner_user_id=None,
            corpus_type="curated",
            filename="unrelated.md",
            object_key="unrelated",
            mime_type="text/markdown",
            parse_status="success",
            sha256="0" * 64,
            source_url=None,
            trusted_level=3,
        )
    )
    session.commit()

    seed_evaluation_corpus(
        session,
        corpus_dir=CORPUS,
        embedding_client=DeterministicEmbeddingClient(),
        reset=True,
    )

    assert session.get(Document, "unrelated-doc") is not None
