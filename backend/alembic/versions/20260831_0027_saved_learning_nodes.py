"""persist saved learning nodes

Revision ID: 20260831_0027
Revises: 20260831_0026
"""

import sqlalchemy as sa
from alembic import op


revision = "20260831_0027"
down_revision = "20260831_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_learning_nodes",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("goal_id", sa.String(), nullable=False),
        sa.Column("knowledge_node_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_id", "goal_id"],
            ["learning_goals.user_id", "learning_goals.id"],
            name="fk_saved_learning_nodes_user_goal",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_node_id"],
            ["knowledge_nodes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "goal_id",
            "knowledge_node_id",
            name="pk_saved_learning_nodes",
        ),
    )


def downgrade() -> None:
    op.drop_table("saved_learning_nodes")
