"""add user authentication identity

Revision ID: 20260716_0011
Revises: 20260711_0010
"""

from alembic import op
import sqlalchemy as sa

revision = "20260716_0011"
down_revision = "20260711_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicates = connection.execute(sa.text("SELECT lower(trim(email)) FROM users GROUP BY lower(trim(email)) HAVING count(*) > 1")).scalars().all()
    if duplicates:
        raise RuntimeError(f"cannot normalize duplicate user emails: {', '.join(duplicates)}")
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("password_hash", sa.Text(), nullable=True))
        batch.add_column(sa.Column("normalized_email", sa.String(), nullable=True))
        batch.add_column(sa.Column("role", sa.String(), nullable=False, server_default="learner"))
        batch.add_column(sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    connection.execute(sa.text("UPDATE users SET normalized_email = lower(trim(email))"))
    with op.batch_alter_table("users") as batch:
        batch.alter_column("normalized_email", nullable=False)
        batch.create_unique_constraint("uq_users_normalized_email", ["normalized_email"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_normalized_email", type_="unique")
        for name in ["last_login_at", "password_changed_at", "token_version", "role", "normalized_email", "password_hash"]:
            batch.drop_column(name)
