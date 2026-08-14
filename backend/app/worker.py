from __future__ import annotations

import math
import os

from celery import Celery

from backend.app.db import SessionLocal
from backend.app.application.document_service import (
    claim_dispatchable_document_upload_events,
    process_document_upload,
    process_document_upload_event,
    release_document_upload_event,
)
from backend.app.application.embedding_reindex_service import (
    claim_pending_embedding_reindex_events,
    process_embedding_reindex_event,
    release_embedding_reindex_event,
)
from backend.app.core.exceptions import DocumentProcessingUnavailable
from backend.app.routers.config import get_secret_store


def _document_outbox_dispatch_interval_seconds() -> float:
    raw_value = os.getenv("DOCUMENT_OUTBOX_DISPATCH_INTERVAL_SECONDS")
    try:
        interval = float(raw_value.strip()) if raw_value is not None and raw_value.strip() else 15.0
    except ValueError:
        return 15.0
    if not math.isfinite(interval):
        return 15.0
    return max(1.0, interval)


celery_app = Celery(
    "adaptive_tutor_worker",
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)
celery_app.conf.beat_schedule = {
    "dispatch-pending-document-uploads": {
        "task": "documents.dispatch_pending",
        "schedule": _document_outbox_dispatch_interval_seconds(),
    },
    "dispatch-pending-embedding-reindexes": {
        "task": "documents.dispatch_embedding_reindex",
        "schedule": _document_outbox_dispatch_interval_seconds(),
    },
}


def dispatch_document_upload_event(session, event_id: str) -> bool:
    claims = claim_dispatchable_document_upload_events(session, event_id=event_id, limit=1)
    session.commit()
    if not claims:
        return False
    claim = claims[0]
    try:
        process_document_upload_task.delay(event_id)
    except Exception as exc:
        release_document_upload_event(
            session,
            event_id=event_id,
            lease_token=claim.lease_token,
            error_type=type(exc).__name__,
        )
        session.commit()
        raise DocumentProcessingUnavailable("document processing queue is unavailable") from exc
    return True


@celery_app.task(name="documents.dispatch_pending")
def dispatch_pending_document_uploads(limit: int = 100) -> dict:
    with SessionLocal() as session:
        claims = claim_dispatchable_document_upload_events(session, limit=limit)
        session.commit()

    dispatched = 0
    failed = 0
    for claim in claims:
        try:
            process_document_upload_task.delay(claim.event_id)
            dispatched += 1
        except Exception as exc:
            failed += 1
            with SessionLocal() as session:
                release_document_upload_event(
                    session,
                    event_id=claim.event_id,
                    lease_token=claim.lease_token,
                    error_type=type(exc).__name__,
                )
                session.commit()
    return {"claimed": len(claims), "dispatched": dispatched, "failed": failed}


@celery_app.task(name="documents.process_upload")
def process_document_upload_task(document_event_id: str) -> dict:
    secret_store = get_secret_store()
    with SessionLocal() as session:
        if document_event_id.startswith("outbox-"):
            result = process_document_upload_event(
                session,
                event_id=document_event_id,
                secret_store=secret_store,
            )
        else:
            result = process_document_upload(
                session,
                document_id=document_event_id,
                secret_store=secret_store,
            )
        session.commit()
        return result


@celery_app.task(name="documents.dispatch_embedding_reindex")
def dispatch_pending_embedding_reindexes(limit: int = 100) -> dict:
    with SessionLocal() as session:
        claims = claim_pending_embedding_reindex_events(session, limit=limit)
        session.commit()
    dispatched = 0
    failed = 0
    for claim in claims:
        try:
            process_embedding_reindex_task.delay(claim.event_id)
            dispatched += 1
        except Exception as exc:
            failed += 1
            with SessionLocal() as session:
                release_embedding_reindex_event(
                    session,
                    event_id=claim.event_id,
                    lease_token=claim.lease_token,
                    error_type=type(exc).__name__,
                )
                session.commit()
    return {"claimed": len(claims), "dispatched": dispatched, "failed": failed}


@celery_app.task(name="documents.reindex_embedding")
def process_embedding_reindex_task(event_id: str) -> dict:
    with SessionLocal() as session:
        result = process_embedding_reindex_event(
            session,
            event_id=event_id,
            secret_store=get_secret_store(),
        )
        session.commit()
        return result
