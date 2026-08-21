from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db import Base, enable_sqlite_foreign_keys
from backend.app.domain.rag.retrieval import QueryAnalyzer, RetrievalRequest
from backend.app.infrastructure.persistence.repositories.rag_retrievers import (
    SQLAlchemyVectorRetriever,
)
from backend.app.models import Document, DocumentChunk, DocumentIndexVersion
from evals.chunking_v3 import ChunkingDocument, ChunkingQuery, EvidenceAnchor


class _ProviderEmbeddingClient:
    provider_identity = "openai-compatible:eval-test"
    model = "text-embedding-eval-test"
    dimensions = 3

    def embed(self, text: str) -> list[float]:
        del text
        return [1.0, 0.0, 0.0]


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed_version(
    session: Session,
    *,
    version_id: str,
    status: str,
    completed: bool = True,
    provider: str = _ProviderEmbeddingClient.provider_identity,
) -> None:
    document_id = f"document-{version_id}"
    session.add(
        Document(
            id=document_id,
            owner_user_id=None,
            corpus_type="curated",
            filename=f"{version_id}.md",
            object_key=f"evals/{version_id}.md",
            mime_type="text/markdown",
            parse_status="success",
            sha256=(version_id * 64)[:64],
            trusted_level=3,
        )
    )
    session.add(
        DocumentIndexVersion(
            id=version_id,
            document_id=document_id,
            build_key=f"build-{version_id}",
            status=status,
            chunk_schema_version="v3",
            chunker_version="hybrid-chunking-v3.1",
            embedding_provider=provider,
            embedding_model=_ProviderEmbeddingClient.model,
            embedding_dimensions=3,
            build_attempt=1,
            chunk_count=1,
            completed_at=datetime.now(timezone.utc) if completed else None,
        )
    )
    session.flush()
    session.add(
        DocumentChunk(
            id=f"chunk-{version_id}",
            document_id=document_id,
            index_version_id=version_id,
            chunk_index=1,
            content=f"{version_id} evidence",
            token_count=2,
            embedding=[1.0, 0.0, 0.0],
            embedding_vector="[1,0,0]",
            metadata_json={},
            citation_label=f"{version_id} chunk 1",
        )
    )
    session.flush()


def test_explicit_retriever_reads_only_requested_completed_versions_without_changing_active_state() -> None:
    from evals.adapters.explicit_index_retriever import ExplicitIndexVersionVectorRetriever

    session = _session()
    _seed_version(session, version_id="ready-index", status="ready")
    _seed_version(session, version_id="active-index", status="active")
    client = _ProviderEmbeddingClient()
    request = RetrievalRequest(query="evidence", top_k=5)

    production = SQLAlchemyVectorRetriever(session, client).retrieve(
        request,
        query=request.query,
        analysis=QueryAnalyzer().analyze(request.query),
    )
    assert [candidate.index_version_id for candidate in production] == ["active-index"]

    retriever = ExplicitIndexVersionVectorRetriever(
        session,
        embedding_client=client,
        index_version_ids=("ready-index",),
    )
    candidates = retriever.retrieve(
        request,
        query=request.query,
        analysis=QueryAnalyzer().analyze(request.query),
    )

    assert [candidate.index_version_id for candidate in candidates] == ["ready-index"]
    assert session.get(DocumentIndexVersion, "ready-index").status == "ready"
    assert session.get(DocumentIndexVersion, "active-index").status == "active"


@pytest.mark.parametrize(
    ("status", "completed", "error"),
    (
        ("building", False, "not completed"),
        ("failed", True, "not completed"),
        ("ready", False, "not completed"),
    ),
)
def test_explicit_retriever_rejects_noncompleted_or_failed_versions(
    status: str,
    completed: bool,
    error: str,
) -> None:
    from evals.adapters.explicit_index_retriever import (
        ExplicitIndexVersionError,
        ExplicitIndexVersionVectorRetriever,
    )

    session = _session()
    _seed_version(session, version_id="candidate-index", status=status, completed=completed)

    with pytest.raises(ExplicitIndexVersionError, match=error):
        ExplicitIndexVersionVectorRetriever(
            session,
            embedding_client=_ProviderEmbeddingClient(),
            index_version_ids=("candidate-index",),
        )


def test_explicit_retriever_rejects_identity_and_request_scope_drift() -> None:
    from evals.adapters.explicit_index_retriever import (
        ExplicitIndexVersionError,
        ExplicitIndexVersionVectorRetriever,
    )

    session = _session()
    _seed_version(session, version_id="expected-index", status="ready")
    _seed_version(
        session,
        version_id="other-provider-index",
        status="ready",
        provider="openai-compatible:other-provider",
    )
    client = _ProviderEmbeddingClient()

    with pytest.raises(ExplicitIndexVersionError, match="embedding identity"):
        ExplicitIndexVersionVectorRetriever(
            session,
            embedding_client=client,
            index_version_ids=("expected-index", "other-provider-index"),
        )

    retriever = ExplicitIndexVersionVectorRetriever(
        session,
        embedding_client=client,
        index_version_ids=("expected-index",),
    )
    scoped_request = RetrievalRequest(
        query="evidence",
        filters={"index_version_ids": ("other-provider-index",)},
    )
    with pytest.raises(ExplicitIndexVersionError, match="request index scope"):
        retriever.retrieve(
            scoped_request,
            query=scoped_request.query,
            analysis=QueryAnalyzer().analyze(scoped_request.query),
        )


def test_provider_phase1_builds_ready_indexes_and_uses_explicit_vector_only_path() -> None:
    from evals.runner.chunking_v3_provider import (
        assert_no_candidate_is_active,
        evaluate_provider_query,
        seed_provider_variant_index,
    )

    session = _session()
    source = "# Retrieval note\n\nThe provider-backed anchor is independently mapped."
    document = ChunkingDocument(
        document_id="provider-document",
        filename="provider-document.md",
        split="development",
        source_type="markdown",
        source_sha256="a" * 64,
    )
    query = ChunkingQuery(
        query_id="provider-query",
        document_id=document.document_id,
        split="development",
        query="provider-backed anchor",
        gold_evidence_anchors=("provider-anchor",),
    )
    anchor = EvidenceAnchor.create(
        anchor_id="provider-anchor",
        document_id=document.document_id,
        text="The provider-backed anchor is independently mapped.",
        source_locator="paragraph:2",
    )

    index = seed_provider_variant_index(
        session,
        documents=((document, source),),
        variant="A",
        embedding_client=_ProviderEmbeddingClient(),
    )
    result = evaluate_provider_query(
        session,
        index=index,
        query=query,
        anchors=(anchor,),
        embedding_client=_ProviderEmbeddingClient(),
    )

    assert index.index_version_ids
    assert result["fixed_k"]["5"]["evidence_recall"] == 1.0
    assert_no_candidate_is_active(session, (index,))
    assert {
        session.get(DocumentIndexVersion, version_id).status
        for version_id in index.index_version_ids
    } == {"ready"}


def test_provider_phase1_requires_explicit_remote_and_isolated_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evals.runner.chunking_v3_provider import require_provider_backed_isolation
    from evals.runner.evaluation_config import EvaluationSafetyError

    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embeddings.example/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "evaluation-only-key")
    monkeypatch.setenv("EVALUATION_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    with pytest.raises(EvaluationSafetyError, match="--allow-remote"):
        require_provider_backed_isolation(allow_remote=False)
    with pytest.raises(EvaluationSafetyError, match="PostgreSQL with pgvector"):
        require_provider_backed_isolation(allow_remote=True)


def test_explicit_postgresql_vector_statement_uses_production_cosine_distance_and_completed_cohort() -> None:
    from evals.adapters.explicit_index_retriever import (
        build_postgresql_explicit_vector_statement,
    )

    statement = str(build_postgresql_explicit_vector_statement())

    assert "embedding_vector <=> CAST(:query_vector AS halfvec)" in statement
    assert "index_version.id = ANY(CAST(:index_version_ids AS text[]))" in statement
    assert "index_version.status IN ('ready', 'active', 'retired')" in statement
    assert "index_version.completed_at IS NOT NULL" in statement
    assert "CAST(:user_id AS text) IS NOT NULL" in statement
