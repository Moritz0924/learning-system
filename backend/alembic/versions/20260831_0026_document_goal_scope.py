"""scope user documents to learning goals

Revision ID: 20260831_0026
Revises: 20260821_0025
"""

import sqlalchemy as sa
from alembic import op


revision = "20260831_0026"
down_revision = "20260821_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("goal_id", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_documents_owner_goal",
            "learning_goals",
            ["owner_user_id", "goal_id"],
            ["user_id", "id"],
        )
    op.create_index(
        "ix_documents_owner_goal_created",
        "documents",
        ["owner_user_id", "goal_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_owner_goal_created", table_name="documents")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("fk_documents_owner_goal", type_="foreignkey")
        batch_op.drop_column("goal_id")
