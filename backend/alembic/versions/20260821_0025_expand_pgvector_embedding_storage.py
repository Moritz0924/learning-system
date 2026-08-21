"""expand pgvector storage for Embedding-3

Revision ID: 20260821_0025
Revises: 20260821_0024
"""

from alembic import op


revision = "20260821_0025"
down_revision = "20260821_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_vector")
    op.execute(
        """
        ALTER TABLE document_chunks
        ALTER COLUMN embedding_vector TYPE halfvec(2048)
        USING CASE
            WHEN embedding_vector IS NULL THEN NULL
            ELSE (left(embedding_vector::text, -1) || repeat(',0', 512) || ']')::halfvec(2048)
        END
        """
    )
    op.execute(
        """
        CREATE INDEX ix_document_chunks_embedding_vector
        ON document_chunks USING ivfflat (embedding_vector halfvec_cosine_ops) WITH (lists = 100)
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_vector")
    op.execute(
        """
        ALTER TABLE document_chunks
        ALTER COLUMN embedding_vector TYPE vector(1536)
        USING CASE
            WHEN embedding_vector IS NULL THEN NULL
            ELSE (
                '[' || array_to_string(
                    (string_to_array(trim(both '[]' from embedding_vector::text), ','))[1:1536],
                    ','
                ) || ']'
            )::vector(1536)
        END
        """
    )
    op.execute(
        """
        CREATE INDEX ix_document_chunks_embedding_vector
        ON document_chunks USING ivfflat (embedding_vector vector_cosine_ops) WITH (lists = 100)
        """
    )
