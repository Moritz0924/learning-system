"""add user AI configuration control plane

Revision ID: 20260814_0020
Revises: 20260731_0019
"""

from alembic import op
import sqlalchemy as sa


revision = "20260814_0020"
down_revision = "20260731_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_model_profiles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("capability", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="openai_compatible"),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_test_status", sa.String(32), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("capability IN ('chat', 'reasoning', 'vision', 'embedding')", name="ck_user_model_profiles_capability"),
        sa.UniqueConstraint("user_id", "name", name="uq_user_model_profile_name"),
    )
    op.create_index("ix_user_model_profiles_user_capability", "user_model_profiles", ["user_id", "capability"])
    op.create_table(
        "user_capability_bindings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("capability", sa.String(16), nullable=False),
        sa.Column("model_profile_id", sa.String(), sa.ForeignKey("user_model_profiles.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("capability IN ('chat', 'reasoning', 'vision', 'embedding')", name="ck_user_capability_bindings_capability"),
        sa.UniqueConstraint("user_id", "capability", name="uq_user_capability_binding"),
    )
    op.create_table(
        "user_prompt_skills",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("default_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_profile_id", sa.String(), sa.ForeignKey("user_model_profiles.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="uq_user_prompt_skill_name"),
    )
    op.create_table(
        "user_mcp_servers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("command", sa.String(2048), nullable=True),
        sa.Column("args_json", sa.JSON(), nullable=False),
        sa.Column("working_directory", sa.String(2048), nullable=True),
        sa.Column("env_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("trust_fingerprint", sa.String(64), nullable=True),
        sa.Column("trusted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", sa.String(32), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("transport IN ('streamable_http', 'stdio')", name="ck_user_mcp_servers_transport"),
        sa.UniqueConstraint("user_id", "name", name="uq_user_mcp_server_name"),
    )
    op.create_table(
        "user_mcp_tools",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("mcp_server_id", sa.String(), sa.ForeignKey("user_mcp_servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("input_schema_json", sa.JSON(), nullable=False),
        sa.Column("annotations_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("mcp_server_id", "name", name="uq_user_mcp_tool_name"),
    )
    op.create_index("ix_user_mcp_tools_server_enabled", "user_mcp_tools", ["mcp_server_id", "enabled"])
    op.create_table(
        "user_secret_references",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("owner_type", sa.String(32), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("slot", sa.String(255), nullable=False),
        sa.Column("secret_ref", sa.String(255), nullable=False),
        sa.Column("configured", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("masked_value", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "owner_type", "owner_id", "slot", name="uq_user_secret_owner_slot"),
        sa.UniqueConstraint("secret_ref", name="uq_user_secret_ref"),
    )
    op.create_table(
        "user_tool_approvals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_id", sa.String(), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("mcp_server_id", sa.String(), sa.ForeignKey("user_mcp_servers.id"), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("result_summary_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'executing', 'completed', 'failed', 'unknown')", name="ck_user_tool_approvals_status"),
        sa.UniqueConstraint("user_id", "request_hash", name="uq_user_tool_approval_request"),
    )
    op.create_index("ix_user_tool_approvals_user_status", "user_tool_approvals", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_user_tool_approvals_user_status", table_name="user_tool_approvals")
    op.drop_table("user_tool_approvals")
    op.drop_table("user_secret_references")
    op.drop_index("ix_user_mcp_tools_server_enabled", table_name="user_mcp_tools")
    op.drop_table("user_mcp_tools")
    op.drop_table("user_mcp_servers")
    op.drop_table("user_prompt_skills")
    op.drop_table("user_capability_bindings")
    op.drop_index("ix_user_model_profiles_user_capability", table_name="user_model_profiles")
    op.drop_table("user_model_profiles")
