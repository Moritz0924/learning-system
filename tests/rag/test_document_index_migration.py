from __future__ import annotations

import json
from importlib import import_module

from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config(database_url: str) -> Config:
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _insert_legacy_document_with_chunks(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, email, display_name, status, created_at,
                    normalized_email, role, token_version
                ) VALUES (
                    'legacy-user', 'legacy@example.test', 'Legacy', 'active',
                    '2026-07-29 08:00:00', 'legacy@example.test', 'learner', 1
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO documents (
                    id, owner_user_id, corpus_type, filename, object_key, mime_type,
                    parse_status, parse_error, size_bytes, parse_error_code, page_count,
                    block_count, parser_version, processing_started_at,
                    processing_completed_at, sha256, source_url, trusted_level, created_at
                ) VALUES (
                    'legacy-document', 'legacy-user', 'user_uploaded', 'legacy.md',
                    'uploads/legacy.md', 'text/markdown', 'success', NULL, 42, NULL, 1,
                    2, 'legacy-parser', NULL, '2026-07-29 08:01:00',
                    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                    NULL, 1, '2026-07-29 08:00:00'
                )
                """
            )
        )
        for chunk_index in (1, 2):
            connection.execute(
                text(
                    """
                    INSERT INTO document_chunks (
                        id, document_id, chunk_index, content, token_count, embedding,
                        metadata, citation_label, created_at, embedding_vector
                    ) VALUES (
                        :id, 'legacy-document', :chunk_index, :content, 2, :embedding,
                        :metadata, :citation_label, '2026-07-29 08:01:00', :embedding_vector
                    )
                    """
                ),
                {
                    "id": f"legacy-chunk-{chunk_index}",
                    "chunk_index": chunk_index,
                    "content": f"legacy content {chunk_index}",
                    "embedding": "[0.1, 0.2, 0.3]",
                    "metadata": "{}",
                    "citation_label": f"legacy.md · chunk {chunk_index}",
                    "embedding_vector": "[0.10000000,0.20000000,0.30000000]",
                },
            )


def test_versioned_index_migration_backfills_and_round_trips(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'index-version-roundtrip.db'}"
    config = _config(database_url)
    upgrade(config, "20260729_0016")
    engine = create_engine(database_url)
    _insert_legacy_document_with_chunks(engine)

    upgrade(config, "head")

    inspector = inspect(engine)
    assert {"document_index_versions", "embedding_cache_entries"} <= set(
        inspector.get_table_names()
    )
    chunk_columns = {column["name"]: column for column in inspector.get_columns("document_chunks")}
    assert chunk_columns["index_version_id"]["nullable"] is False
    assert "uq_document_index_versions_active_document" in {
        index["name"] for index in inspector.get_indexes("document_index_versions")
    }
    assert "uq_document_chunks_index_position" in {
        constraint["name"] for constraint in inspector.get_unique_constraints("document_chunks")
    }
    with engine.connect() as connection:
        version = connection.execute(
            text(
                """
                SELECT id, document_id, build_key, status, chunk_schema_version,
                       chunker_version, embedding_provider, embedding_model, embedding_dimensions,
                       build_attempt, chunk_count
                FROM document_index_versions
                """
            )
        ).one()
        linked_chunks = connection.execute(
            text("SELECT id, index_version_id FROM document_chunks ORDER BY chunk_index")
        ).all()
    assert version.document_id == "legacy-document"
    assert version.build_key == "legacy-v1"
    assert version.status == "active"
    assert version.chunk_schema_version == "legacy-v1"
    assert version.chunker_version == "legacy-split-text-v1"
    assert version.embedding_provider == "legacy-unknown"
    assert version.embedding_model == "legacy-unknown"
    assert version.embedding_dimensions == 1536
    assert version.build_attempt == 1
    assert version.chunk_count == 2
    assert [row.id for row in linked_chunks] == ["legacy-chunk-1", "legacy-chunk-2"]
    assert {row.index_version_id for row in linked_chunks} == {version.id}

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE document_index_versions SET status = 'retired' WHERE id = :id"),
            {"id": version.id},
        )
        connection.execute(
            text(
                """
                INSERT INTO document_index_versions (
                    id, document_id, build_key, status, chunk_schema_version,
                    chunker_version, embedding_provider, embedding_model, embedding_dimensions,
                    chunk_count, error_message, created_at, updated_at,
                    completed_at, activated_at, retired_at
                ) VALUES (
                    'ready-v2', 'legacy-document', 'ready-v2', 'ready', 'v2',
                    'chunking-v2', 'legacy-unknown', 'model-v2', 1536, 1, NULL,
                    '2026-07-29 09:00:00', '2026-07-29 09:00:00',
                    '2026-07-29 09:00:00', NULL, NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO document_chunks (
                    id, document_id, index_version_id, chunk_index, content,
                    token_count, embedding, embedding_vector, metadata,
                    citation_label, created_at
                ) VALUES (
                    'ready-v2-chunk', 'legacy-document', 'ready-v2', 1,
                    'ready v2 content', 3, :embedding, :embedding_vector,
                    :metadata, 'ready · chunk 1', '2026-07-29 09:00:00'
                )
                """
            ),
            {
                "embedding": "[0.1, 0.2, 0.3]",
                "embedding_vector": "[0.10000000,0.20000000,0.30000000]",
                "metadata": json.dumps(
                    {"chunk_schema_version": "v2", "index_version_id": "ready-v2"}
                ),
            },
        )

    downgrade(config, "20260729_0016")

    inspector = inspect(engine)
    assert "document_index_versions" not in inspector.get_table_names()
    assert "embedding_cache_entries" not in inspector.get_table_names()
    assert "index_version_id" not in {
        column["name"] for column in inspector.get_columns("document_chunks")
    }
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT id FROM document_chunks ORDER BY chunk_index")
        ).scalars().all() == ["ready-v2-chunk"]

    upgrade(config, "head")

    inspector = inspect(engine)
    assert "document_index_versions" in inspector.get_table_names()
    assert "index_version_id" in {
        column["name"] for column in inspector.get_columns("document_chunks")
    }
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM document_index_versions "
                "WHERE document_id = 'legacy-document' AND status = 'active'"
            )
        ) == 1
        chunk = connection.execute(
            text(
                "SELECT index_version_id, metadata FROM document_chunks "
                "WHERE document_id = 'legacy-document'"
            )
        ).one()
        metadata = json.loads(chunk.metadata) if isinstance(chunk.metadata, str) else chunk.metadata
        assert metadata["index_version_id"] == chunk.index_version_id
        assert chunk.index_version_id != "ready-v2"
    engine.dispose()


def test_embedding_provider_migration_extends_the_versioned_index_head() -> None:
    migration = import_module(
        "backend.alembic.versions.20260730_0018_embedding_provider_identity"
    )

    assert migration.revision == "20260730_0018"
    assert migration.down_revision == "20260729_0017"
