from __future__ import annotations

import base64
import os

from celery import Celery

from backend.app.db import SessionLocal
from backend.app.services.stage3 import process_document_upload


celery_app = Celery(
    "adaptive_tutor_worker",
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)


@celery_app.task(name="documents.process_upload")
def process_document_upload_task(document_id: str, content_base64: str) -> dict:
    content_bytes = base64.b64decode(content_base64.encode("ascii"), validate=True)
    with SessionLocal() as session:
        result = process_document_upload(session, document_id=document_id, content_bytes=content_bytes)
        session.commit()
        return result
