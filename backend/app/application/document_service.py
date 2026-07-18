from __future__ import annotations

import asyncio
import os
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from uuid import uuid4

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
from backend.app.services.document_parsing.models import OCRResult, ParseStatus
from backend.app.services.document_parsing.exceptions import DocumentParsingError
from backend.app.services.document_parsing.parser import DocumentParser


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
    try:
        storage = object_storage or build_document_object_storage()
    except ObjectStorageUnavailable as exc:
        raise DocumentProcessingUnavailable("document object storage is unavailable") from exc
    try:
        storage.put_bytes(object_key, payload, content_type=mime_type)
    except ObjectStorageUnavailable as exc:
        try:
            storage.delete_bytes(object_key)
        except Exception:
            logger.warning(
                "failed to remove partially uploaded document object",
                extra={"object_key": object_key},
            )
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
            session.rollback()
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
    previously_successful = document.parse_status == "success" and session.scalar(
        select(DocumentChunk.id).where(DocumentChunk.document_id == document_id).limit(1)
    ) is not None
    document.parse_status = "processing"
    document.parse_error = None
    try:
        parsed_chunks = _parse_document_content(
            content_bytes,
            filename=document.filename,
            mime_type=document.mime_type,
            ocr_client=ocr_client,
        )
        chunk_records = _build_document_chunk_records(document=document, parsed_chunks=parsed_chunks)
        document._prepared_document_chunk_records = chunk_records
        session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        _store_document_chunks(session, document=document, parsed_chunks=parsed_chunks)
        document.parse_status = "success"
        document.parse_error = None
        session.flush()
        return {"document_id": document.id, "status": "success", "chunk_count": len(parsed_chunks)}
    except EmbeddingUnavailable as exc:
        document.parse_status = "success" if previously_successful else "pending"
        document.parse_error = str(exc)
        session.flush()
        raise DocumentProcessingUnavailable(str(exc)) from exc
    except (ValueError, DocumentParsingError) as exc:
        document.parse_status = "success" if previously_successful else "failed"
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
    supported_suffixes = {".pdf", ".pptx", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
    if suffix not in supported_suffixes and not normalized_type.startswith("image/") and normalized_type != "application/pdf":
        raise ValueError(f"unsupported document mime type: {mime_type}")
    parser = DocumentParser(ocr_service=_LegacyOCRService(ocr_client) if ocr_client else None)
    try:
        result = asyncio.run(parser.parse_document(content=content_bytes, filename=filename, mime_type=mime_type))
    except DocumentParsingError as exc:
        raise ValueError(str(exc)) from exc
    if result.status is ParseStatus.FAILED:
        if suffix == ".pdf":
            raise ValueError("pdf document contains no extractable text")
        if normalized_type.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}:
            raise ValueError("image OCR produced no text")
        raise ValueError("document parser produced no text")
    parsed: list[dict] = []
    extracted_chars = 0
    for block in result.blocks:
        normalized = _normalize_text(block.text)
        if not normalized:
            continue
        extracted_chars += len(normalized)
        _validate_extracted_text_limit(extracted_chars)
        for chunk_index, chunk in enumerate(split_text(normalized), start=1):
            metadata = block.model_dump(mode="json")
            metadata["chunk_index"] = chunk_index
            metadata["processing_source_type"] = {
                "pdf_text": "pdf",
                "pdf_ocr": "pdf_ocr",
                "ppt_native_text": "ppt_native_text",
                "ppt_ocr": "ppt_ocr",
                "image_ocr": "image_ocr",
            }[block.processing_mode.value]
            metadata["source_type"] = "uploaded_document"
            metadata["parser_version"] = result.parser_version
            metadata["content_sha256"] = result.content_sha256
            parsed.append({"content": chunk, "metadata": metadata})
    _validate_chunk_count(len(parsed))
    if not parsed:
        raise ValueError("document parser produced no text")
    return parsed


class _LegacyOCRService:
    def __init__(self, client: OCRClient) -> None:
        self.client = client

    async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
        text = self.client.extract_text(content, filename=filename).strip()
        return OCRResult(text=text, confidence=None, word_count=len(text.split()), text_char_count=len(text))


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

def _build_document_chunk_records(*, document: Document, parsed_chunks: list[dict]) -> list[DocumentChunk]:
    embedding = build_embedding_client()
    chunk_records: list[DocumentChunk] = []
    for index, parsed in enumerate(parsed_chunks, start=1):
        chunk_content = parsed["content"]
        embedding_values = embedding.embed(chunk_content)
        if len(embedding_values) != EXPECTED_EMBEDDING_DIMENSIONS:
            raise EmbeddingUnavailable(
                f"expected {EXPECTED_EMBEDDING_DIMENSIONS}-dimensional embedding, got {len(embedding_values)}"
            )
        raw_metadata = parsed.get("metadata", {})
        metadata = {**raw_metadata, "untrusted_input": True}
        if not raw_metadata:
            metadata = {"source_type": parsed.get("source_type", "uploaded_document"), "chunk_index": index, "untrusted_input": True}
            if parsed.get("page_number") is not None:
                metadata["page_number"] = parsed["page_number"]
        page_number = metadata.get("page_number")
        block_index = metadata.get("block_index", 1)
        local_chunk_index = metadata.get("chunk_index", index)
        file_type = metadata.get("file_type")
        location = "image" if file_type == "image" else ("slide" if file_type == "pptx" else "page")
        citation_label = f"{document.filename} · {location}"
        if location != "image":
            citation_label += f" {page_number}"
        citation_label += f" · block {block_index} · chunk {local_chunk_index}"
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
    return chunk_records


def _store_document_chunks(session: Session, *, document: Document, parsed_chunks: list[dict]) -> None:
    prepared = getattr(document, "_prepared_document_chunk_records", None)
    if prepared is None:
        prepared = _build_document_chunk_records(document=document, parsed_chunks=parsed_chunks)
    session.add_all(prepared)

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


def get_document_record(session: Session, *, user_id: str, document_id: str) -> dict | None:
    document = session.scalar(
        select(Document).where(Document.id == document_id, Document.owner_user_id == user_id)
    )
    return _document_to_dict(document) if document is not None else None
