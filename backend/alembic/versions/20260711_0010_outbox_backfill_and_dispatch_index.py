"""backfill pending document events and index outbox dispatch

Revision ID: 20260711_0010
Revises: 20260710_0009
Create Date: 2026-07-11
"""

from datetime import datetime
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "20260711_0010"
down_revision = "20260710_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_outbox_events_dispatch_due",
        "outbox_events",
        ["event_type", "status", "available_at"],
        unique=False,
    )
    _backfill_pending_document_events()


def _backfill_pending_document_events() -> None:
    documents = sa.table(
        "documents",
        sa.column("id", sa.String()),
        sa.column("parse_status", sa.String()),
    )
    outbox_events = sa.table(
        "outbox_events",
        sa.column("id", sa.String()),
        sa.column("event_type", sa.String()),
        sa.column("dedupe_key", sa.String()),
        sa.column("payload_json", sa.JSON()),
        sa.column("status", sa.String()),
        sa.column("attempts", sa.Integer()),
        sa.column("available_at", sa.DateTime()),
        sa.column("dispatch_token", sa.String()),
        sa.column("last_error", sa.Text()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    connection = op.get_bind()
    document_ids = connection.scalars(
        sa.select(documents.c.id).where(documents.c.parse_status == "pending")
    ).all()
    existing_keys = set(
        connection.scalars(
            sa.select(outbox_events.c.dedupe_key).where(
                outbox_events.c.dedupe_key.is_not(None)
            )
        ).all()
    )
    now = datetime.utcnow()
    for document_id in document_ids:
        dedupe_key = f"document.process_upload:{document_id}"
        if dedupe_key in existing_keys:
            continue
        connection.execute(
            outbox_events.insert().values(
                id=f"outbox-backfill-{uuid4()}",
                event_type="document.process_upload",
                dedupe_key=dedupe_key,
                payload_json={"document_id": document_id},
                status="pending",
                attempts=0,
                available_at=now,
                dispatch_token=None,
                last_error=None,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    # Backfilled events are operational history and intentionally remain in place.
    op.drop_index("ix_outbox_events_dispatch_due", table_name="outbox_events")
