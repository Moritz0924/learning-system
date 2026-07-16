"""add auth sessions and refresh tokens

Revision ID: 20260716_0012
Revises: 20260716_0011
"""

from alembic import op
import sqlalchemy as sa

revision = "20260716_0012"
down_revision = "20260716_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("auth_sessions", sa.Column("id", sa.String(), primary_key=True), sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False), sa.Column("status", sa.String(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False), sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.Column("revoke_reason", sa.String()), sa.Column("user_agent_hash", sa.String()))
    op.create_index("ix_auth_sessions_user_status", "auth_sessions", ["user_id", "status"])
    op.create_index("ix_auth_sessions_idle_expires_at", "auth_sessions", ["idle_expires_at"])
    op.create_index("ix_auth_sessions_absolute_expires_at", "auth_sessions", ["absolute_expires_at"])
    op.create_table("refresh_tokens", sa.Column("id", sa.String(), primary_key=True), sa.Column("session_id", sa.String(), sa.ForeignKey("auth_sessions.id"), nullable=False), sa.Column("token_hash", sa.String(), nullable=False, unique=True), sa.Column("parent_token_id", sa.String(), sa.ForeignKey("refresh_tokens.id")), sa.Column("replaced_by_token_id", sa.String(), sa.ForeignKey("refresh_tokens.id")), sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("used_at", sa.DateTime(timezone=True)), sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.Column("reuse_detected_at", sa.DateTime(timezone=True)))
    op.create_index("ix_refresh_tokens_session_expires", "refresh_tokens", ["session_id", "expires_at"])
    op.create_index("ix_refresh_tokens_session_used", "refresh_tokens", ["session_id", "used_at"])


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_table("auth_sessions")
