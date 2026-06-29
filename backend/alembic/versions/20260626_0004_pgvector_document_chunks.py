"""add pgvector document chunk embeddings

Revision ID: 20260626_0004
Revises: 20260623_0003
Create Date: 2026-06-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260626_0004"
down_revision = "20260623_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_vector vector(1536)")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_vector "
            "ON document_chunks USING ivfflat (embedding_vector vector_cosine_ops) WITH (lists = 100)"
        )
        return
    op.add_column("document_chunks", sa.Column("embedding_vector", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_vector")
        op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding_vector")
        return
    op.drop_column("document_chunks", "embedding_vector")
