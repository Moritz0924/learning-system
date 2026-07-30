from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.db import Base, enable_sqlite_foreign_keys
from backend.app.models import Document, DocumentChunk, DocumentIndexVersion
from backend.app.services.embeddings import DeterministicEmbeddingClient
from evals.models import GoldChunkMap


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "evals" / "datasets" / "learning_qa_v1.jsonl"
CORPUS = ROOT / "evals" / "corpus" / "learning_qa_v1"
V2_GOLD_MAP = ROOT / "evals" / "generated" / "learning_qa_v1_chunk_map_v2.json"


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_chunking_v2_gold_map_is_deterministic_and_matches_checked_in_asset() -> None:
    from evals.runner.gold_chunk_map_v2 import (
        build_gold_chunk_map_v2,
        compute_chunking_v2_config_hash,
        gold_chunk_map_v2_json,
        validate_gold_chunk_map_v2,
    )

    first = build_gold_chunk_map_v2(DATASET, corpus_dir=CORPUS)
    second = build_gold_chunk_map_v2(DATASET, corpus_dir=CORPUS)
    checked_in = GoldChunkMap.model_validate_json(V2_GOLD_MAP.read_text(encoding="utf-8"))

    assert first == second == checked_in
    assert first.chunking_config_hash == compute_chunking_v2_config_hash()
    assert validate_gold_chunk_map_v2(first, corpus_dir=CORPUS) == []
    assert gold_chunk_map_v2_json(first) == V2_GOLD_MAP.read_text(encoding="utf-8")
    assert all(
        chunk_id.startswith("chunk-")
        for case in first.cases.values()
        for group in case.evidence_groups
        for chunk_id in group.acceptable_chunk_ids
    )


def test_v2_seed_adds_and_activates_separate_index_versions_without_removing_v1_chunks() -> None:
    from evals.runner.corpus_seed import seed_evaluation_corpus
    from evals.runner.corpus_seed_v2 import seed_evaluation_corpus_v2
    from evals.runner.gold_chunk_map import build_corpus_chunks
    from evals.runner.gold_chunk_map_v2 import build_corpus_chunks_v2

    session = _session()
    embedding = DeterministicEmbeddingClient()
    seed_evaluation_corpus(
        session,
        corpus_dir=CORPUS,
        embedding_client=embedding,
        reset=False,
    )
    _, v1_chunks = build_corpus_chunks(CORPUS)
    v1_ids = {chunk.chunk_id for chunks in v1_chunks.values() for chunk in chunks}

    first = seed_evaluation_corpus_v2(
        session,
        corpus_dir=CORPUS,
        embedding_client=embedding,
        reset=False,
    )
    second = seed_evaluation_corpus_v2(
        session,
        corpus_dir=CORPUS,
        embedding_client=embedding,
        reset=False,
    )
    _, v2_chunks = build_corpus_chunks_v2(CORPUS)
    v2_ids = {chunk.chunk_id for chunks in v2_chunks.values() for chunk in chunks}
    stored_chunks = session.scalars(select(DocumentChunk)).all()
    versions = session.scalars(select(DocumentIndexVersion)).all()

    assert first.reused is False
    assert second.reused is True
    assert first.document_count == second.document_count == 5
    assert first.chunk_count == second.chunk_count == len(v2_ids)
    assert v1_ids | v2_ids == {chunk.id for chunk in stored_chunks}
    assert v1_ids.isdisjoint(v2_ids)
    assert all(
        chunk.metadata_json["chunk_schema_version"] == "v2"
        for chunk in stored_chunks
        if chunk.id in v2_ids
    )
    assert all(
        len([version for version in versions if version.document_id == document_id]) == 2
        for document_id in {version.document_id for version in versions}
    )
    assert all(
        len(
            [
                version
                for version in versions
                if version.document_id == document_id and version.status == "active"
            ]
        )
        == 1
        for document_id in {version.document_id for version in versions}
    )
    assert all(
        version.chunk_schema_version == "v2"
        for version in versions
        if version.status == "active"
    )


def test_v2_gold_generator_ids_match_production_persisted_chunk_identity() -> None:
    from backend.app.application.document_index_service import DocumentIndexService
    from evals.runner.gold_chunk_map_v2 import (
        EVALUATION_V2_CHUNKER_VERSION,
        build_corpus_chunks_v2,
        evaluation_v2_index_build_key,
    )

    session = _session()
    embedding = DeterministicEmbeddingClient()
    manifest, chunks_by_document = build_corpus_chunks_v2(
        CORPUS,
        embedding_client=embedding,
    )
    item = manifest.documents[0]
    expected = chunks_by_document[item.document_id]
    session.add(
        Document(
            id=item.document_id,
            owner_user_id=None,
            corpus_type="curated",
            filename=item.filename,
            object_key=f"evals/identity-parity/{item.filename}",
            mime_type="text/markdown",
            parse_status="success",
            sha256=item.sha256,
            trusted_level=3,
        )
    )
    session.flush()

    version = DocumentIndexService(session, embedding).build_index(
        user_id=None,
        document_id=item.document_id,
        build_key=evaluation_v2_index_build_key(item, embedding_client=embedding),
        chunks=expected,
        chunker_version=EVALUATION_V2_CHUNKER_VERSION,
    )
    stored = session.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.index_version_id == version.id)
        .order_by(DocumentChunk.chunk_index)
    ).all()

    assert [chunk.id for chunk in stored] == [chunk.chunk_id for chunk in expected]
    assert [chunk.metadata_json["content_hash"] for chunk in stored] == [
        chunk.content_hash for chunk in expected
    ]
    assert [chunk.metadata_json["previous_chunk_id"] for chunk in stored] == [
        chunk.previous_chunk_id for chunk in expected
    ]
    assert [chunk.metadata_json["next_chunk_id"] for chunk in stored] == [
        chunk.next_chunk_id for chunk in expected
    ]
    assert all(chunk.metadata_json["index_version_id"] == version.id for chunk in stored)
