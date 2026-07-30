"""scope embedding indexes and caches by provider identity

Revision ID: 20260730_0018
Revises: 20260729_0017
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260730_0018"
down_revision = "20260729_0017"
branch_labels = None
depends_on = None


LEGACY_PROVIDER = "legacy-unknown"


def upgrade() -> None:
    bind = op.get_bind()
    provider_column = sa.Column(
        "embedding_provider",
        sa.String(length=128),
        nullable=False,
        server_default=LEGACY_PROVIDER,
    )
    cache_provider_column = sa.Column(
        "embedding_provider",
        sa.String(length=128),
        nullable=False,
        server_default=LEGACY_PROVIDER,
    )
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "document_index_versions",
            recreate="always",
        ) as batch:
            batch.add_column(provider_column)
            batch.drop_constraint(
                "uq_document_index_versions_document_build",
                type_="unique",
            )
            batch.create_unique_constraint(
                "uq_document_index_versions_document_build",
                ["document_id", "build_key", "embedding_provider"],
            )
        op.drop_index(
            "ix_embedding_cache_entries_lookup",
            table_name="embedding_cache_entries",
        )
        with op.batch_alter_table(
            "embedding_cache_entries",
            recreate="always",
        ) as batch:
            batch.add_column(cache_provider_column)
            batch.drop_constraint(
                "uq_embedding_cache_model_dimensions_hash",
                type_="unique",
            )
            batch.create_unique_constraint(
                "uq_embedding_cache_provider_model_dimensions_hash",
                [
                    "embedding_provider",
                    "embedding_model",
                    "dimensions",
                    "content_hash",
                ],
            )
    else:
        op.add_column("document_index_versions", provider_column)
        op.drop_constraint(
            "uq_document_index_versions_document_build",
            "document_index_versions",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_document_index_versions_document_build",
            "document_index_versions",
            ["document_id", "build_key", "embedding_provider"],
        )
        op.drop_index(
            "ix_embedding_cache_entries_lookup",
            table_name="embedding_cache_entries",
        )
        op.add_column("embedding_cache_entries", cache_provider_column)
        op.drop_constraint(
            "uq_embedding_cache_model_dimensions_hash",
            "embedding_cache_entries",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_embedding_cache_provider_model_dimensions_hash",
            "embedding_cache_entries",
            [
                "embedding_provider",
                "embedding_model",
                "dimensions",
                "content_hash",
            ],
        )
    op.create_index(
        "ix_embedding_cache_entries_lookup",
        "embedding_cache_entries",
        [
            "embedding_provider",
            "embedding_model",
            "dimensions",
            "content_hash",
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    _collapse_provider_duplicates(bind)
    op.drop_index(
        "ix_embedding_cache_entries_lookup",
        table_name="embedding_cache_entries",
    )
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "embedding_cache_entries",
            recreate="always",
        ) as batch:
            batch.drop_constraint(
                "uq_embedding_cache_provider_model_dimensions_hash",
                type_="unique",
            )
            batch.create_unique_constraint(
                "uq_embedding_cache_model_dimensions_hash",
                ["embedding_model", "dimensions", "content_hash"],
            )
            batch.drop_column("embedding_provider")
        with op.batch_alter_table(
            "document_index_versions",
            recreate="always",
        ) as batch:
            batch.drop_constraint(
                "uq_document_index_versions_document_build",
                type_="unique",
            )
            batch.create_unique_constraint(
                "uq_document_index_versions_document_build",
                ["document_id", "build_key"],
            )
            batch.drop_column("embedding_provider")
    else:
        op.drop_constraint(
            "uq_embedding_cache_provider_model_dimensions_hash",
            "embedding_cache_entries",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_embedding_cache_model_dimensions_hash",
            "embedding_cache_entries",
            ["embedding_model", "dimensions", "content_hash"],
        )
        op.drop_column("embedding_cache_entries", "embedding_provider")
        op.drop_constraint(
            "uq_document_index_versions_document_build",
            "document_index_versions",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_document_index_versions_document_build",
            "document_index_versions",
            ["document_id", "build_key"],
        )
        op.drop_column("document_index_versions", "embedding_provider")
    op.create_index(
        "ix_embedding_cache_entries_lookup",
        "embedding_cache_entries",
        ["embedding_model", "dimensions", "content_hash"],
    )


def _collapse_provider_duplicates(bind) -> None:
    bind.execute(
        sa.text(
            """
            DELETE FROM embedding_cache_entries
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY embedding_model, dimensions, content_hash
                               ORDER BY id
                           ) AS duplicate_rank
                    FROM embedding_cache_entries
                ) AS ranked
                WHERE duplicate_rank > 1
            )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM document_index_versions
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY document_id, build_key
                               ORDER BY
                                   CASE WHEN status = 'active' THEN 0 ELSE 1 END,
                                   updated_at DESC,
                                   id
                           ) AS duplicate_rank
                    FROM document_index_versions
                ) AS ranked
                WHERE duplicate_rank > 1
            )
            """
        )
    )
