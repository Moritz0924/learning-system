from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db import Base, enable_sqlite_foreign_keys
from backend.app.infrastructure.persistence.repositories.rag_repository import SQLAlchemyRagRepository
from backend.app.services.embeddings import DeterministicEmbeddingClient, EmbeddingUnavailable
from evals.runner.corpus_seed import seed_evaluation_corpus


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "evals" / "corpus" / "learning_qa_v1"


def _seeded_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    session = Session(engine)
    seed_evaluation_corpus(
        session,
        corpus_dir=CORPUS,
        embedding_client=DeterministicEmbeddingClient(),
        reset=False,
    )
    return session


def test_retrieve_timed_preserves_v1_vector_telemetry_while_production_uses_selected_context() -> None:
    session = _seeded_session()
    repository = SQLAlchemyRagRepository(session, DeterministicEmbeddingClient())

    timed = repository.retrieve_timed("RAG 检索阶段", top_k=3)
    production = repository.retrieve("RAG 检索阶段", top_k=3)

    assert [chunk.chunk_id for chunk in timed.chunks] == [
        candidate.chunk_id
        for candidate in repository.last_retrieval_result.candidates_by_source["vector"]
    ]
    assert [chunk.chunk_id for chunk in production] == [
        candidate.chunk_id
        for candidate in repository.last_retrieval_trace.selected_candidates
    ][:3]
    assert timed.status == "grounded"
    assert timed.backend == "local_json_embedding"
    assert len(timed.scores) == len(timed.chunks) == 3
    assert all(score.score_kind == "cosine_similarity" for score in timed.scores)
    assert all(score.higher_is_better for score in timed.scores)
    assert timed.total_latency_ms >= timed.postprocess_latency_ms >= 0
    assert timed.embedding_latency_ms is not None


def test_retrieve_timed_marks_embedding_failure_as_failed() -> None:
    class BrokenEmbedding:
        def embed(self, text: str) -> list[float]:
            raise EmbeddingUnavailable("down")

    repository = SQLAlchemyRagRepository(_seeded_session(), BrokenEmbedding())

    result = repository.retrieve_timed("question", top_k=5)

    assert result.status == "failed"
    assert result.error_code == "embedding_unavailable"
    assert result.chunks == []
    assert result.scores == []


def test_evaluation_repository_scope_excludes_other_curated_documents() -> None:
    repository = SQLAlchemyRagRepository(
        _seeded_session(),
        DeterministicEmbeddingClient(),
        allowed_document_ids={"eval-doc-rag-001"},
    )

    result = repository.retrieve_timed("question", top_k=5)

    assert result.chunks
    assert {chunk.document_id for chunk in result.chunks} == {"eval-doc-rag-001"}


def test_pgvector_scores_are_reported_as_cosine_distance(monkeypatch) -> None:
    class MappingResult:
        @staticmethod
        def mappings():
            return [{
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "content": "evidence",
                "citation_label": "source",
                "metadata_json": {},
                "source_title": "doc.md",
                "source_url": None,
                "trusted_level": 3,
                "corpus_type": "curated",
                "distance": 0.25,
            }]

    class PostgreSQLSession:
        @staticmethod
        def get_bind():
            return type("Bind", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()})()

        @staticmethod
        def begin_nested():
            return nullcontext()

        @staticmethod
        def execute(statement, parameters):
            assert "AS distance" in str(statement)
            return MappingResult()

    monkeypatch.setenv("RAG_RETRIEVAL_BACKEND", "pgvector")
    repository = SQLAlchemyRagRepository(PostgreSQLSession(), DeterministicEmbeddingClient())

    result = repository.retrieve_timed("question", top_k=1)

    assert result.scores[0].raw_value == 0.25
    assert result.scores[0].score_kind == "cosine_distance"
    assert result.scores[0].higher_is_better is False
