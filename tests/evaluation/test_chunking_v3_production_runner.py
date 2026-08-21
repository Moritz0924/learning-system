from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db import Base, enable_sqlite_foreign_keys
from backend.app.models import DocumentChunk, DocumentIndexVersion
from evals.chunking_v3 import ChunkingDocument, ChunkingQuery, EvidenceAnchor


class _ProviderEmbeddingClient:
    provider_identity = "openai-compatible:production-eval-test"
    model = "text-embedding-production-eval-test"
    dimensions = 3

    def embed(self, text: str) -> list[float]:
        del text
        return [1.0, 0.0, 0.0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    return Session(engine)


def _source_fixture() -> tuple[ChunkingDocument, str, ChunkingQuery, EvidenceAnchor]:
    source = """# Retrieval note

The production orchestrator anchor is independently mapped.

The supporting paragraph provides a distinct retrieval sentence.
"""
    document = ChunkingDocument(
        document_id="production-document",
        filename="production-document.md",
        split="test",
        source_type="markdown",
        source_sha256="b" * 64,
    )
    query = ChunkingQuery(
        query_id="production-query",
        document_id=document.document_id,
        split="test",
        query="production orchestrator anchor",
        gold_evidence_anchors=("production-anchor",),
    )
    anchor = EvidenceAnchor.create(
        anchor_id="production-anchor",
        document_id=document.document_id,
        text="The production orchestrator anchor is independently mapped.",
        source_locator="paragraph:2",
    )
    return document, source, query, anchor


def _seed_previous_active(session: Session, *, document_id: str) -> str:
    version_id = "preexisting-active-index"
    session.add(
        DocumentIndexVersion(
            id=version_id,
            document_id=document_id,
            build_key="preexisting-active-build",
            status="active",
            chunk_schema_version="v3",
            chunker_version="hybrid-chunking-v3.1",
            embedding_provider=_ProviderEmbeddingClient.provider_identity,
            embedding_model=_ProviderEmbeddingClient.model,
            embedding_dimensions=3,
            build_attempt=1,
            chunk_count=1,
            completed_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    session.add(
        DocumentChunk(
            id="preexisting-active-chunk",
            document_id=document_id,
            index_version_id=version_id,
            chunk_index=1,
            content="old active evidence",
            token_count=3,
            embedding=[1.0, 0.0, 0.0],
            embedding_vector="[1,0,0]",
            metadata_json={},
            citation_label="old active chunk 1",
        )
    )
    session.commit()
    return version_id


def _indexes(session: Session):
    from evals.runner.chunking_v3_provider import seed_provider_variant_index

    document, source, query, anchor = _source_fixture()
    client = _ProviderEmbeddingClient()
    baseline = seed_provider_variant_index(
        session,
        documents=((document, source),),
        variant="A",
        embedding_client=client,
    )
    best = seed_provider_variant_index(
        session,
        documents=((document, source),),
        variant="C",
        embedding_client=client,
    )
    old_active = _seed_previous_active(
        session,
        document_id="chunking-v3-source-production-document",
    )
    return client, {"A": baseline, "C": best}, query, anchor, old_active


def test_production_runner_uses_real_orchestrator_sequentially_and_restores_active_indexes() -> None:
    from evals.runner.chunking_v3_production import run_production_a_vs_best

    session = _session()
    client, indexes, query, anchor, old_active = _indexes(session)

    output = run_production_a_vs_best(
        session,
        indexes=indexes,
        baseline="A",
        best="C",
        queries=(query,),
        anchors=(anchor,),
        embedding_client=client,
    )

    assert output["production_orchestrator"] == "RetrievalOrchestrator"
    assert output["variants"] == ["A", "C"]
    assert output["per_query"][query.query_id]["A"]["fixed_k"]["5"]["evidence_recall"] == 1.0
    traces = output["traces"][query.query_id]
    assert traces["A"]["query_rewriter"] == "not_configured"
    assert traces["A"]["reranker"].endswith("HeuristicReranker")
    assert {entry["source"] for entry in traces["A"]["source_attempts"]} == {
        "vector",
        "keyword",
        "metadata",
    }
    assert {
        version.id
        for version in session.query(DocumentIndexVersion)
        .filter(DocumentIndexVersion.status == "active")
        .all()
    } == {old_active}


def test_production_runner_restores_active_indexes_when_retrieval_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.infrastructure.persistence.repositories.rag_repository import (
        SQLAlchemyRagRepository,
    )
    from evals.runner.chunking_v3_production import run_production_a_vs_best

    session = _session()
    client, indexes, query, anchor, old_active = _indexes(session)

    def fail_retrieve(self, request):
        raise RuntimeError("forced evaluation failure")

    monkeypatch.setattr(SQLAlchemyRagRepository, "retrieve_v2", fail_retrieve)
    with pytest.raises(RuntimeError, match="forced evaluation failure"):
        run_production_a_vs_best(
            session,
            indexes=indexes,
            baseline="A",
            best="C",
            queries=(query,),
            anchors=(anchor,),
            embedding_client=client,
        )

    assert {
        version.id
        for version in session.query(DocumentIndexVersion)
        .filter(DocumentIndexVersion.status == "active")
        .all()
    } == {old_active}


def test_production_runner_restores_an_initially_empty_active_state() -> None:
    from evals.runner.chunking_v3_production import run_production_a_vs_best

    session = _session()
    document, source, query, anchor = _source_fixture()
    client = _ProviderEmbeddingClient()
    from evals.runner.chunking_v3_provider import seed_provider_variant_index

    indexes = {
        "A": seed_provider_variant_index(
            session,
            documents=((document, source),),
            variant="A",
            embedding_client=client,
        ),
        "C": seed_provider_variant_index(
            session,
            documents=((document, source),),
            variant="C",
            embedding_client=client,
        ),
    }

    output = run_production_a_vs_best(
        session,
        indexes=indexes,
        baseline="A",
        best="C",
        queries=(query,),
        anchors=(anchor,),
        embedding_client=client,
    )

    assert session.query(DocumentIndexVersion).filter(
        DocumentIndexVersion.status == "active"
    ).count() == 0


def test_empty_state_restore_does_not_retire_unrelated_active_indexes() -> None:
    from backend.app.models import Document
    from evals.runner.chunking_v3_production import run_production_a_vs_best
    from evals.runner.chunking_v3_provider import seed_provider_variant_index

    session = _session()
    document, source, query, anchor = _source_fixture()
    client = _ProviderEmbeddingClient()
    indexes = {
        variant: seed_provider_variant_index(
            session,
            documents=((document, source),),
            variant=variant,
            embedding_client=client,
        )
        for variant in ("A", "C")
    }
    session.add(
        Document(
            id="unrelated-document",
            owner_user_id=None,
            corpus_type="curated",
            filename="unrelated.md",
            object_key="evals/unrelated.md",
            mime_type="text/markdown",
            parse_status="success",
            sha256="c" * 64,
            trusted_level=3,
        )
    )
    session.flush()
    session.add(
        DocumentIndexVersion(
            id="unrelated-active-index",
            document_id="unrelated-document",
            build_key="unrelated-build",
            status="active",
            chunk_schema_version="v3",
            chunker_version="hybrid-chunking-v3.1",
            embedding_provider=client.provider_identity,
            embedding_model=client.model,
            embedding_dimensions=client.dimensions,
            build_attempt=1,
            chunk_count=0,
            completed_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    output = run_production_a_vs_best(
        session,
        indexes=indexes,
        baseline="A",
        best="C",
        queries=(query,),
        anchors=(anchor,),
        embedding_client=client,
    )

    assert session.get(DocumentIndexVersion, "unrelated-active-index").status == "active"
    assert all(
        candidate_id != "unrelated-active-chunk"
        for trace in output["traces"].values()
        for variant_trace in trace.values()
        for attempt in variant_trace["source_attempts"]
        for candidate_id in attempt["candidate_ids"]
    )
