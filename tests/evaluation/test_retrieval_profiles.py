from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from backend.app.db import Base, enable_sqlite_foreign_keys
from backend.app.models import DocumentChunk, DocumentIndexVersion
from backend.app.services.embeddings import DeterministicEmbeddingClient
from evals.runner.corpus_seed import seed_evaluation_corpus
from evals.runner.corpus_seed_v2 import seed_evaluation_corpus_v2


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


def test_active_evaluation_index_schema_rejects_v1_v2_mismatches() -> None:
    from evals.runner.retrieval_profile import validate_evaluation_index_schema

    session = _session()
    embedding = DeterministicEmbeddingClient()
    seed_evaluation_corpus(
        session,
        corpus_dir=CORPUS,
        embedding_client=embedding,
        reset=False,
    )

    validate_evaluation_index_schema(
        session,
        corpus_dir=CORPUS,
        index_schema="legacy-v1",
        embedding_client=embedding,
    )
    with pytest.raises(ValueError, match="active evaluation index mismatch.*v2"):
        validate_evaluation_index_schema(
            session,
            corpus_dir=CORPUS,
            index_schema="v2",
            embedding_client=embedding,
        )

    seed_evaluation_corpus_v2(
        session,
        corpus_dir=CORPUS,
        embedding_client=embedding,
        reset=False,
    )

    validate_evaluation_index_schema(
        session,
        corpus_dir=CORPUS,
        index_schema="v2",
        embedding_client=embedding,
    )
    with pytest.raises(ValueError, match="active evaluation index mismatch.*legacy-v1"):
        validate_evaluation_index_schema(
            session,
            corpus_dir=CORPUS,
            index_schema="legacy-v1",
            embedding_client=embedding,
        )


def test_active_evaluation_index_schema_rejects_missing_expected_v2_chunk() -> None:
    from evals.runner.retrieval_profile import validate_evaluation_index_schema

    session = _session()
    embedding = DeterministicEmbeddingClient()
    seed_evaluation_corpus_v2(
        session,
        corpus_dir=CORPUS,
        embedding_client=embedding,
        reset=False,
    )
    active_version_id = session.scalar(
        select(DocumentIndexVersion.id)
        .where(DocumentIndexVersion.status == "active")
        .order_by(DocumentIndexVersion.id)
        .limit(1)
    )
    missing_chunk_id = session.scalar(
        select(DocumentChunk.id)
        .where(DocumentChunk.index_version_id == active_version_id)
        .order_by(DocumentChunk.id)
        .limit(1)
    )
    session.execute(delete(DocumentChunk).where(DocumentChunk.id == missing_chunk_id))
    session.flush()

    with pytest.raises(ValueError, match="active evaluation index mismatch.*chunk ids"):
        validate_evaluation_index_schema(
            session,
            corpus_dir=CORPUS,
            index_schema="v2",
            embedding_client=embedding,
        )


@pytest.mark.parametrize(
    ("provider", "model", "dimensions", "reason"),
    (
        ("provider-b", "deterministic-model-a", 3, "embedding provider"),
        ("provider-a", "deterministic-model-b", 3, "embedding model"),
        ("provider-a", "deterministic-model-a", 4, "embedding dimensions"),
    ),
)
def test_legacy_profile_rejects_embedding_identity_mismatches(
    provider: str,
    model: str,
    dimensions: int,
    reason: str,
) -> None:
    from evals.runner.retrieval_profile import validate_evaluation_index_schema

    session = _session()
    seed_evaluation_corpus(
        session,
        corpus_dir=CORPUS,
        embedding_client=_embedding_client("provider-a"),
        reset=False,
    )

    with pytest.raises(ValueError, match=f"legacy-v1.*{reason}"):
        validate_evaluation_index_schema(
            session,
            corpus_dir=CORPUS,
            index_schema="legacy-v1",
            embedding_client=_embedding_client(
                provider,
                model=model,
                dimensions=dimensions,
            ),
        )
