"""add versioned document indexes and embedding cache

Revision ID: 20260729_0017
Revises: 20260729_0016
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from alembic import op
import sqlalchemy as sa


revision = "20260729_0017"
down_revision = "20260729_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_index_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("build_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("chunk_schema_version", sa.String(length=32), nullable=False),
        sa.Column("chunker_version", sa.String(length=64), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('building', 'ready', 'active', 'retired', 'failed')",
            name="ck_document_index_versions_status",
        ),
        sa.CheckConstraint(
            "embedding_dimensions > 0",
            name="ck_document_index_versions_positive_dimensions",
        ),
        sa.CheckConstraint(
            "chunk_count >= 0",
            name="ck_document_index_versions_nonnegative_chunk_count",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_index_versions_document_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_index_versions"),
        sa.UniqueConstraint(
            "document_id",
            "build_key",
            name="uq_document_index_versions_document_build",
        ),
        sa.UniqueConstraint(
            "document_id",
            "id",
            name="uq_document_index_versions_document_id_id",
        ),
    )
    op.create_index(
        "ix_document_index_versions_document_status",
        "document_index_versions",
        ["document_id", "status"],
    )
    op.create_index(
        "uq_document_index_versions_active_document",
        "document_index_versions",
        ["document_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "embedding_cache_entries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "dimensions > 0",
            name="ck_embedding_cache_entries_positive_dimensions",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name="ck_embedding_cache_entries_content_hash",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_embedding_cache_entries"),
        sa.UniqueConstraint(
            "embedding_model",
            "dimensions",
            "content_hash",
            name="uq_embedding_cache_model_dimensions_hash",
        ),
    )
    op.create_index(
        "ix_embedding_cache_entries_lookup",
        "embedding_cache_entries",
        ["embedding_model", "dimensions", "content_hash"],
    )

    op.add_column(
        "document_chunks",
        sa.Column("index_version_id", sa.String(), nullable=True),
    )
    _backfill_legacy_indexes()

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("document_chunks", recreate="always") as batch:
            batch.alter_column("index_version_id", existing_type=sa.String(), nullable=False)
            batch.create_foreign_key(
                "fk_document_chunks_index_document",
                "document_index_versions",
                ["document_id", "index_version_id"],
                ["document_id", "id"],
                ondelete="CASCADE",
            )
            batch.create_unique_constraint(
                "uq_document_chunks_index_position",
                ["index_version_id", "chunk_index"],
            )
            batch.create_check_constraint(
                "ck_document_chunks_positive_index",
                "chunk_index > 0",
            )
    else:
        op.alter_column(
            "document_chunks",
            "index_version_id",
            existing_type=sa.String(),
            nullable=False,
        )
        op.create_foreign_key(
            "fk_document_chunks_index_document",
            "document_chunks",
            "document_index_versions",
            ["document_id", "index_version_id"],
            ["document_id", "id"],
            ondelete="CASCADE",
        )
        op.create_unique_constraint(
            "uq_document_chunks_index_position",
            "document_chunks",
            ["index_version_id", "chunk_index"],
        )
        op.create_check_constraint(
            "ck_document_chunks_positive_index",
            "document_chunks",
            "chunk_index > 0",
        )
    op.create_index(
        "ix_document_chunks_document_index",
        "document_chunks",
        ["document_id", "index_version_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    _collapse_versions_for_downgrade(bind)
    op.drop_index("ix_document_chunks_document_index", table_name="document_chunks")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("document_chunks", recreate="always") as batch:
            batch.drop_constraint("ck_document_chunks_positive_index", type_="check")
            batch.drop_constraint("uq_document_chunks_index_position", type_="unique")
            batch.drop_constraint("fk_document_chunks_index_document", type_="foreignkey")
            batch.drop_column("index_version_id")
    else:
        op.drop_constraint(
            "ck_document_chunks_positive_index",
            "document_chunks",
            type_="check",
        )
        op.drop_constraint(
            "uq_document_chunks_index_position",
            "document_chunks",
            type_="unique",
        )
        op.drop_constraint(
            "fk_document_chunks_index_document",
            "document_chunks",
            type_="foreignkey",
        )
        op.drop_column("document_chunks", "index_version_id")

    op.drop_index("ix_embedding_cache_entries_lookup", table_name="embedding_cache_entries")
    op.drop_table("embedding_cache_entries")
    op.drop_index(
        "uq_document_index_versions_active_document",
        table_name="document_index_versions",
    )
    op.drop_index(
        "ix_document_index_versions_document_status",
        table_name="document_index_versions",
    )
    op.drop_table("document_index_versions")


def _backfill_legacy_indexes() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT document_id, count(*) AS chunk_count
            FROM document_chunks
            GROUP BY document_id
            ORDER BY document_id
            """
        )
    ).mappings()
    now = datetime.now(timezone.utc)
    for row in rows:
        document_id = str(row["document_id"])
        index_version_id = _legacy_index_version_id(document_id)
        bind.execute(
            sa.text(
                """
                INSERT INTO document_index_versions (
                    id, document_id, build_key, status, chunk_schema_version,
                    chunker_version, embedding_model, embedding_dimensions,
                    chunk_count, error_message, created_at, updated_at,
                    completed_at, activated_at, retired_at
                ) VALUES (
                    :id, :document_id, 'legacy-v1', 'active', 'legacy-v1',
                    'legacy-split-text-v1', 'legacy-unknown', 1536,
                    :chunk_count, NULL, :now, :now, :now, :now, NULL
                )
                """
            ),
            {
                "id": index_version_id,
                "document_id": document_id,
                "chunk_count": int(row["chunk_count"]),
                "now": now,
            },
        )
        bind.execute(
            sa.text(
                """
                UPDATE document_chunks
                SET index_version_id = :index_version_id
                WHERE document_id = :document_id
                """
            ),
            {"index_version_id": index_version_id, "document_id": document_id},
        )
        _set_chunk_metadata_index_version(bind, document_id, index_version_id)


def _legacy_index_version_id(document_id: str) -> str:
    identity = f"document-index-legacy-v1\0{document_id}".encode("utf-8")
    return f"index-{sha256(identity).hexdigest()[:32]}"


def _collapse_versions_for_downgrade(bind) -> None:
    rows = list(
        bind.execute(
            sa.text(
                """
                SELECT DISTINCT v.id, v.document_id, v.status,
                       v.completed_at, v.updated_at, v.created_at
                FROM document_index_versions AS v
                JOIN document_chunks AS c ON c.index_version_id = v.id
                ORDER BY v.document_id, v.id
                """
            )
        ).mappings()
    )
    priority = {"active": 0, "ready": 1, "retired": 2, "building": 3, "failed": 4}
    selected: dict[str, dict] = {}
    for row in rows:
        document_id = str(row["document_id"])
        rank = (
            priority.get(str(row["status"]), 99),
            _sortable_timestamp(row["completed_at"] or row["updated_at"] or row["created_at"]),
            str(row["id"]),
        )
        current = selected.get(document_id)
        if current is None:
            selected[document_id] = {"row": row, "rank": rank}
            continue
        current_rank = current["rank"]
        if rank[0] < current_rank[0] or (rank[0] == current_rank[0] and rank[1:] > current_rank[1:]):
            selected[document_id] = {"row": row, "rank": rank}

    for document_id, value in selected.items():
        selected_id = str(value["row"]["id"])
        bind.execute(
            sa.text(
                """
                DELETE FROM document_chunks
                WHERE document_id = :document_id AND index_version_id != :selected_id
                """
            ),
            {"document_id": document_id, "selected_id": selected_id},
        )
        _set_chunk_metadata_index_version(bind, document_id, None)


def _set_chunk_metadata_index_version(
    bind,
    document_id: str,
    index_version_id: str | None,
) -> None:
    chunk_table = sa.table(
        "document_chunks",
        sa.column("id", sa.String()),
        sa.column("document_id", sa.String()),
        sa.column("metadata", sa.JSON()),
    )
    rows = bind.execute(
        sa.select(chunk_table.c.id, chunk_table.c.metadata).where(
            chunk_table.c.document_id == document_id
        )
    ).mappings()
    for row in rows:
        metadata = row["metadata"]
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                continue
        if not isinstance(metadata, dict):
            continue
        normalized = dict(metadata)
        if index_version_id is None:
            normalized.pop("index_version_id", None)
        else:
            normalized["index_version_id"] = index_version_id
        bind.execute(
            chunk_table.update()
            .where(chunk_table.c.id == row["id"])
            .values(metadata=normalized)
        )


def _sortable_timestamp(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")
