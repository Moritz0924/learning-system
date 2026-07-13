from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from uuid import uuid4

from pypdf import PdfReader
from PIL import Image
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from adaptive_tutor.phase2.ports import OCRClient
from adaptive_tutor.phase2.rag import split_text
from backend.app.application.serialization import _document_to_dict
from backend.app.core.exceptions import DocumentProcessingUnavailable, DocumentUploadTooLarge
from backend.app.core.runtime_config import normalize_runtime_mode
from backend.app.infrastructure.persistence.repositories.rag_repository import _vector_literal
from backend.app.models import Document, DocumentChunk, OutboxEvent
from backend.app.services.embeddings import EmbeddingUnavailable, build_embedding_client
from backend.app.services.object_storage import (
    DocumentObjectStorage,
    ObjectStorageUnavailable,
    build_document_object_storage,
)
from backend.app.services.ocr import build_ocr_client


EXPECTED_EMBEDDING_DIMENSIONS = 1536
DOCUMENT_UPLOAD_EVENT_TYPE = "document.process_upload"
DEFAULT_DOCUMENT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_DOCUMENT_MAX_PDF_PAGES = 200
DEFAULT_DOCUMENT_MAX_IMAGE_PIXELS = 40_000_000
DEFAULT_DOCUMENT_MAX_EXTRACTED_CHARS = 2_000_000
DEFAULT_DOCUMENT_MAX_CHUNKS = 2_000
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentUploadEventClaim:
    event_id: str
    lease_token: str


def create_document_record(
    session: Session,
    *,
    user_id: str,
    filename: str,
    mime_type: str,
    content: str = "",
    content_bytes: bytes | None = None,
    source_url: str | None = None,
    processing_mode: str | None = None,
    object_storage: DocumentObjectStorage | None = None,
) -> dict:
    safe_filename = _safe_upload_filename(filename)
    payload = content_bytes if content_bytes is not None else content.encode("utf-8")
    if not payload:
        raise ValueError("document upload content is required")
    max_upload_bytes = _positive_int_env("DOCUMENT_MAX_UPLOAD_BYTES", DEFAULT_DOCUMENT_MAX_UPLOAD_BYTES)
    if len(payload) > max_upload_bytes:
        raise DocumentUploadTooLarge(f"document upload exceeds {max_upload_bytes} byte limit")
    digest = sha256(payload).hexdigest()
    document_id = f"doc-{uuid4()}"
    object_key = (
        f"uploads/{_safe_object_key_component(user_id)}/"
        f"{document_id}-{digest[:12]}-{_safe_object_key_component(safe_filename)}"
    )
    storage = object_storage or build_document_object_storage()
    try:
        storage.put_bytes(object_key, payload, content_type=mime_type)
    except ObjectStorageUnavailable as exc:
        raise DocumentProcessingUnavailable("document object storage is unavailable") from exc
    committed = False
    try:
        document = Document(
            id=document_id,
            owner_user_id=user_id,
            corpus_type="user_uploaded",
            filename=safe_filename,
            object_key=object_key,
            mime_type=mime_type,
            parse_status="pending",
            parse_error=None,
            sha256=digest,
            source_url=source_url,
            trusted_level=1,
        )
        session.add(document)
        session.flush()
        mode = normalize_runtime_mode(
            processing_mode or os.getenv("DOCUMENT_PROCESSING_MODE"),
            default="inline",
        )
        if mode == "defer":
            _enqueue_document_processing(session, document.id)
            session.commit()
            committed = True
            return _document_to_dict(document)
        if mode == "celery":
            event = _enqueue_document_processing(session, document.id)
            session.commit()
            committed = True
            try:
                from backend.app.worker import dispatch_document_upload_event

                dispatch_document_upload_event(session, event.id)
            except Exception as exc:
                logger.warning(
                    "initial document event dispatch failed; periodic dispatcher will retry",
                    extra={"outbox_event_id": event.id, "error_type": type(exc).__name__},
                )
            return _document_to_dict(document)
        process_document_upload(session, document_id=document.id, content_bytes=payload)
        session.commit()
        committed = True
        return _document_to_dict(document)
    except Exception:
        if not committed:
            try:
                storage.delete_bytes(object_key)
            except Exception:
                logger.exception("failed to remove uncommitted document object", extra={"object_key": object_key})
        raise

def process_document_upload_event(session: Session, *, event_id: str) -> dict:
    event = session.get(OutboxEvent, event_id)
    if event is None:
        raise LookupError(f"outbox event {event_id} not found")
    if event.event_type != "document.process_upload":
        event.status = "failed"
        event.dispatch_token = None
        event.last_error = f"unexpected document outbox event type: {event.event_type}"
        session.flush()
        return {
            "event_id": event.id,
            "status": "failed",
            "already_processed": False,
            "error": event.last_error,
        }
    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    document_id = payload.get("document_id")
    if isinstance(document_id, str):
        document_id = document_id.strip()
    if not document_id:
        event.status = "failed"
        event.dispatch_token = None
        event.last_error = "document outbox event missing document_id"
        session.flush()
        return {"event_id": event.id, "status": "failed", "already_processed": False}
    if event.status == "succeeded":
        return {
            "event_id": event.id,
            "document_id": document_id,
            "status": "succeeded",
            "already_processed": True,
        }
    if event.status == "failed":
        return {
            "event_id": event.id,
            "document_id": document_id,
            "status": "failed",
            "already_processed": True,
            "error": event.last_error,
        }
    document = session.get(Document, document_id)
    if document is None:
        event.status = "failed"
        event.dispatch_token = None
        event.last_error = f"document {document_id} not found"
        session.flush()
        return {
            "event_id": event.id,
            "document_id": document_id,
            "status": "failed",
            "already_processed": False,
            "error": event.last_error,
        }
    if document is not None and document.parse_status == "success":
        event.status = "succeeded"
        event.dispatch_token = None
        event.last_error = None
        session.flush()
        return {
            "event_id": event.id,
            "document_id": document_id,
            "status": "succeeded",
            "already_processed": True,
        }
    now = datetime.utcnow()
    if event.status == "pending" and event.available_at > now:
        return {
            "event_id": event.id,
            "document_id": document_id,
            "status": "pending",
            "already_processed": False,
            "deferred": True,
            "available_at": event.available_at.isoformat(),
        }
    claim = session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event.id,
            or_(
                OutboxEvent.status == "queued",
                and_(OutboxEvent.status == "pending", OutboxEvent.available_at <= now),
            ),
        )
        .values(
            status="processing",
            attempts=OutboxEvent.attempts + 1,
            dispatch_token=None,
            last_error=None,
        )
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        session.refresh(event)
        return {
            "event_id": event.id,
            "document_id": document_id,
            "status": event.status,
            "already_processed": True,
            "error": event.last_error,
        }
    session.refresh(event)
    try:
        with session.begin_nested():
            result = process_document_upload(session, document_id=document_id)
    except Exception as exc:
        logger.exception("document upload processing failed", extra={"outbox_event_id": event.id})
        session.refresh(event)
        last_error = _document_processing_public_error(exc)
        max_attempts = _document_processing_max_attempts()
        document = session.get(Document, document_id)
        if event.attempts >= max_attempts:
            last_error = f"document processing failed after {event.attempts} attempts: {last_error}"
            event.status = "failed"
            if document is not None:
                document.parse_status = "failed"
                document.parse_error = last_error
        else:
            event.status = "pending"
            event.available_at = _next_document_processing_available_at(event.attempts)
            if document is not None:
                document.parse_status = "pending"
                document.parse_error = last_error
        event.last_error = last_error
        session.flush()
        return {
            "event_id": event.id,
            "document_id": document_id,
            "status": event.status,
            "already_processed": False,
            "error": event.last_error,
        }
    event.status = "succeeded" if result["status"] == "success" else "failed"
    event.last_error = None if event.status == "succeeded" else result.get("parse_error", result["status"])
    session.flush()
    return {
        "event_id": event.id,
        "document_id": document_id,
        "status": event.status,
        "document_status": result["status"],
        "chunk_count": result["chunk_count"],
        "parse_error": result.get("parse_error"),
        "already_processed": False,
    }

def process_document_upload(
    session: Session,
    *,
    document_id: str,
    content_bytes: bytes | None = None,
    object_storage: DocumentObjectStorage | None = None,
    ocr_client: OCRClient | None = None,
) -> dict:
    document = session.get(Document, document_id)
    if document is None:
        raise LookupError(f"document {document_id} not found")
    if content_bytes is None:
        storage = object_storage or build_document_object_storage()
        try:
            content_bytes = storage.get_bytes(document.object_key)
        except ObjectStorageUnavailable as exc:
            raise DocumentProcessingUnavailable("document object storage is unavailable") from exc
    document.parse_status = "processing"
    document.parse_error = None
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    session.flush()
    try:
        parsed_chunks = _parse_document_content(
            content_bytes,
            filename=document.filename,
            mime_type=document.mime_type,
            ocr_client=ocr_client,
        )
        _store_document_chunks(session, document=document, parsed_chunks=parsed_chunks)
        document.parse_status = "success"
        document.parse_error = None
        session.flush()
        return {"document_id": document.id, "status": "success", "chunk_count": len(parsed_chunks)}
    except EmbeddingUnavailable as exc:
        document.parse_status = "pending"
        document.parse_error = str(exc)
        session.flush()
        raise DocumentProcessingUnavailable(str(exc)) from exc
    except ValueError as exc:
        document.parse_status = "failed"
        document.parse_error = str(exc)
        session.flush()
        return {"document_id": document.id, "status": "failed", "chunk_count": 0, "parse_error": document.parse_error}

def _parse_document_content(
    content_bytes: bytes,
    *,
    filename: str,
    mime_type: str,
    ocr_client: OCRClient | None = None,
) -> list[dict]:
    normalized_type = mime_type.lower()
    suffix = Path(filename).suffix.lower()
    if normalized_type in {"text/markdown", "text/plain", "application/markdown"} or suffix in {
        ".md",
        ".markdown",
    }:
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("markdown document must be utf-8 text") from exc
        normalized = _normalize_text(content)
        if not normalized:
            raise ValueError("document contains no text")
        _validate_extracted_text_limit(len(normalized))
        chunks = split_text(normalized)
        _validate_chunk_count(len(chunks))
        return [
            {"content": chunk, "source_type": "markdown", "page_number": None}
            for chunk in chunks
        ]
    if normalized_type == "application/pdf" or suffix == ".pdf":
        return _parse_pdf_content(content_bytes)
    if normalized_type.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}:
        _validate_image_pixel_limit(content_bytes)
        client = ocr_client or build_ocr_client()
        normalized = _normalize_text(client.extract_text(content_bytes, filename=filename))
        if not normalized:
            raise ValueError("image OCR produced no text")
        _validate_extracted_text_limit(len(normalized))
        chunks = split_text(normalized)
        _validate_chunk_count(len(chunks))
        return [
            {"content": chunk, "source_type": "image_ocr", "page_number": None}
            for chunk in chunks
        ]
    raise ValueError(f"unsupported document mime type: {mime_type}")

def _parse_pdf_content(content_bytes: bytes) -> list[dict]:
    try:
        reader = PdfReader(BytesIO(content_bytes))
    except Exception as exc:
        raise ValueError("pdf document could not be parsed") from exc
    max_pages = _positive_int_env("DOCUMENT_MAX_PDF_PAGES", DEFAULT_DOCUMENT_MAX_PDF_PAGES)
    if len(reader.pages) > max_pages:
        raise ValueError(f"pdf document exceeds {max_pages} page limit")
    parsed: list[dict] = []
    extracted_chars = 0
    for page_number, page in enumerate(reader.pages, start=1):
        normalized = _normalize_text(page.extract_text() or "")
        if not normalized:
            continue
        extracted_chars += len(normalized)
        _validate_extracted_text_limit(extracted_chars)
        parsed.extend(
            {"content": chunk, "source_type": "pdf", "page_number": page_number}
            for chunk in split_text(normalized)
        )
        _validate_chunk_count(len(parsed))
    if not parsed:
        raise ValueError("pdf document contains no extractable text")
    return parsed


def _validate_extracted_text_limit(extracted_chars: int) -> None:
    max_chars = _positive_int_env(
        "DOCUMENT_MAX_EXTRACTED_CHARS",
        DEFAULT_DOCUMENT_MAX_EXTRACTED_CHARS,
    )
    if extracted_chars > max_chars:
        raise ValueError(f"document extracted text exceeds {max_chars} character limit")


def _validate_chunk_count(chunk_count: int) -> None:
    max_chunks = _positive_int_env("DOCUMENT_MAX_CHUNKS", DEFAULT_DOCUMENT_MAX_CHUNKS)
    if chunk_count > max_chunks:
        raise ValueError(f"document exceeds {max_chunks} chunk limit")


def _validate_image_pixel_limit(content_bytes: bytes) -> None:
    try:
        with Image.open(BytesIO(content_bytes)) as image:
            pixel_count = image.width * image.height
    except Exception as exc:
        raise ValueError("image document could not be inspected") from exc
    max_pixels = _positive_int_env("DOCUMENT_MAX_IMAGE_PIXELS", DEFAULT_DOCUMENT_MAX_IMAGE_PIXELS)
    if pixel_count > max_pixels:
        raise ValueError(f"image document exceeds {max_pixels} pixel limit")

def _normalize_text(content: str) -> str:
    return "\n".join(line.strip("# ").strip() for line in content.splitlines() if line.strip())

def _safe_upload_filename(filename: str) -> str:
    normalized = filename.replace("\\", "/").strip()
    safe_name = PurePosixPath(normalized).name.strip()
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError("document filename is required")
    return safe_name

def _safe_object_key_component(value: str) -> str:
    encoded = quote(value.strip(), safe="-_.~")
    if encoded in {"", ".", ".."}:
        encoded = f"id-{sha256(value.encode('utf-8')).hexdigest()[:16]}"
    return encoded

def _document_processing_max_attempts() -> int:
    try:
        return max(1, int(os.getenv("DOCUMENT_PROCESSING_MAX_ATTEMPTS", "3")))
    except ValueError:
        return 3

def _document_processing_retry_delay_seconds() -> int:
    try:
        return max(0, int(os.getenv("DOCUMENT_PROCESSING_RETRY_DELAY_SECONDS", "60")))
    except ValueError:
        return 60


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default

def _next_document_processing_available_at(attempts: int) -> datetime:
    delay_seconds = _document_processing_retry_delay_seconds() * max(1, attempts)
    return datetime.utcnow() + timedelta(seconds=delay_seconds)


def _document_processing_public_error(exc: Exception) -> str:
    if isinstance(exc, SQLAlchemyError):
        return "document processing database error"
    if isinstance(exc, DocumentProcessingUnavailable):
        return str(exc)
    return f"document processing failed: {type(exc).__name__}"


def claim_dispatchable_document_upload_events(
    session: Session,
    *,
    event_id: str | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> list[DocumentUploadEventClaim]:
    current_time = now or datetime.utcnow()
    query = (
        select(OutboxEvent)
        .where(
            OutboxEvent.event_type == DOCUMENT_UPLOAD_EVENT_TYPE,
            OutboxEvent.status.in_({"pending", "queued"}),
            OutboxEvent.available_at <= current_time,
        )
        .order_by(OutboxEvent.available_at, OutboxEvent.created_at, OutboxEvent.id)
        .limit(max(1, limit))
        .with_for_update(skip_locked=True)
    )
    if event_id is not None:
        query = query.where(OutboxEvent.id == event_id)
    events = list(session.scalars(query))
    lease_until = current_time + timedelta(seconds=_document_dispatch_lease_seconds())
    claims: list[DocumentUploadEventClaim] = []
    for event in events:
        lease_token = f"dispatch-{uuid4()}"
        event.status = "queued"
        event.available_at = lease_until
        event.dispatch_token = lease_token
        event.last_error = None
        claims.append(DocumentUploadEventClaim(event_id=event.id, lease_token=lease_token))
    session.flush()
    return claims


def release_document_upload_event(
    session: Session,
    *,
    event_id: str,
    lease_token: str,
    error_type: str,
) -> bool:
    released = session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event_id,
            OutboxEvent.status == "queued",
            OutboxEvent.dispatch_token == lease_token,
        )
        .values(
            status="pending",
            available_at=_next_document_processing_available_at(1),
            dispatch_token=None,
            last_error=f"document event dispatch failed: {error_type}",
        )
        .execution_options(synchronize_session=False)
    )
    return released.rowcount == 1


def _document_dispatch_lease_seconds() -> int:
    try:
        return max(1, int(os.getenv("DOCUMENT_OUTBOX_DISPATCH_LEASE_SECONDS", "300")))
    except ValueError:
        return 300

def _store_document_chunks(session: Session, *, document: Document, parsed_chunks: list[dict]) -> None:
    embedding = build_embedding_client()
    chunk_records: list[DocumentChunk] = []
    for index, parsed in enumerate(parsed_chunks, start=1):
        chunk_content = parsed["content"]
        embedding_values = embedding.embed(chunk_content)
        if len(embedding_values) != EXPECTED_EMBEDDING_DIMENSIONS:
            raise EmbeddingUnavailable(
                f"expected {EXPECTED_EMBEDDING_DIMENSIONS}-dimensional embedding, got {len(embedding_values)}"
            )
        metadata = {"source_type": parsed["source_type"], "untrusted_input": True, "chunk_index": index}
        if parsed.get("page_number") is not None:
            metadata["page_number"] = parsed["page_number"]
        citation_label = (
            f"{document.filename} page {parsed['page_number']} chunk {index}"
            if parsed.get("page_number") is not None
            else f"{document.filename} chunk {index}"
        )
        chunk_records.append(
            DocumentChunk(
                id=f"chunk-{uuid4()}",
                document_id=document.id,
                chunk_index=index,
                content=chunk_content,
                token_count=len(chunk_content.split()),
                embedding=embedding_values,
                embedding_vector=_vector_literal(embedding_values),
                metadata_json=metadata,
                citation_label=citation_label,
            )
        )
    session.add_all(chunk_records)

def _enqueue_document_processing(session: Session, document_id: str) -> OutboxEvent:
    dedupe_key = f"{DOCUMENT_UPLOAD_EVENT_TYPE}:{document_id}"
    existing = session.scalar(select(OutboxEvent).where(OutboxEvent.dedupe_key == dedupe_key))
    if existing is not None:
        return existing
    event = OutboxEvent(
        id=f"outbox-{uuid4()}",
        event_type=DOCUMENT_UPLOAD_EVENT_TYPE,
        dedupe_key=dedupe_key,
        payload_json={"document_id": document_id},
        status="pending",
        attempts=0,
    )
    session.add(event)
    session.flush()
    return event

def list_document_records(session: Session, *, user_id: str) -> list[dict]:
    documents = session.scalars(select(Document).where(Document.owner_user_id == user_id)).all()
    return [_document_to_dict(document) for document in documents]
