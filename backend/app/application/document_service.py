from __future__ import annotations

import asyncio
import os
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from adaptive_tutor.phase2.ports import OCRClient
from backend.app.application.serialization import _document_to_dict
from backend.app.core.exceptions import DocumentProcessingUnavailable, DocumentUploadTooLarge
from backend.app.core.runtime_config import normalize_runtime_mode
from backend.app.domain.rag.chunking import (
    DEFAULT_CHUNK_POLICY,
    ChunkDraft,
    ChunkMetadataBuilder,
    ChunkType,
    ChunkerRegistry,
    normalize_chunk_text,
)
from backend.app.infrastructure.persistence.repositories.rag_repository import _vector_literal
from backend.app.models import Document, DocumentChunk, OutboxEvent
from backend.app.services.embeddings import EmbeddingUnavailable, build_embedding_client
from backend.app.services.object_storage import (
    DocumentObjectStorage,
    ObjectStorageUnavailable,
    build_document_object_storage,
)
from backend.app.services.document_parsing.models import OCRResult, ParseStatus
from backend.app.services.document_parsing.exceptions import (
    CorruptedDocumentError,
    DocumentParsingError,
    DocumentTooLargeError,
    EncryptedPDFError,
    FileTypeMismatchError,
    UnsupportedDocumentTypeError,
)
from backend.app.services.document_parsing.parser import DocumentParser


EXPECTED_EMBEDDING_DIMENSIONS = 1536
DOCUMENT_UPLOAD_EVENT_TYPE = "document.process_upload"
DEFAULT_DOCUMENT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_DOCUMENT_MAX_PDF_PAGES = 200
DEFAULT_DOCUMENT_MAX_IMAGE_PIXELS = 40_000_000
DEFAULT_DOCUMENT_MAX_EXTRACTED_CHARS = 2_000_000
DEFAULT_DOCUMENT_MAX_CHUNKS = 2_000
DOCUMENT_ERROR_EMPTY_FILE = "document.empty_file"
DOCUMENT_ERROR_FILE_TOO_LARGE = "document.file_too_large"
DOCUMENT_ERROR_UNSUPPORTED_TYPE = "document.unsupported_type"
DOCUMENT_ERROR_INVALID_FILENAME = "document.invalid_filename"
DOCUMENT_ERROR_CORRUPTED_PDF = "document.corrupted_pdf"
DOCUMENT_ERROR_ENCRYPTED_PDF = "document.encrypted_pdf"
DOCUMENT_ERROR_CORRUPTED_PPTX = "document.corrupted_pptx"
DOCUMENT_ERROR_OCR_NO_TEXT = "document.ocr_no_text"
DOCUMENT_ERROR_PARSER_NO_TEXT = "document.parser_no_text"
DOCUMENT_ERROR_OBJECT_STORAGE_UNAVAILABLE = "document.object_storage_unavailable"
DOCUMENT_ERROR_EMBEDDING_UNAVAILABLE = "document.embedding_unavailable"
DOCUMENT_ERROR_ATTEMPTS_EXHAUSTED = "document.processing_attempts_exhausted"
DOCUMENT_ERROR_INTERNAL = "document.processing_internal_error"
logger = logging.getLogger(__name__)
_CHUNKER_REGISTRY = ChunkerRegistry.default()
_CHUNK_METADATA_BUILDER = ChunkMetadataBuilder(DEFAULT_CHUNK_POLICY)


@dataclass(frozen=True)
class DocumentUploadEventClaim:
    event_id: str
    lease_token: str


@dataclass(frozen=True)
class ParsedDocumentContent:
    chunks: list[dict]
    page_count: int
    block_count: int
    parser_version: str


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
            size_bytes=len(payload),
            parse_error_code=None,
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
    document.parse_status = "processing"
    document.processing_started_at = _utcnow()
    document.processing_completed_at = None
    document.parse_error = None
    document.parse_error_code = None
    session.flush()
    try:
        with session.begin_nested():
            result = process_document_upload(session, document_id=document_id)
    except Exception as exc:
        logger.exception("document upload processing failed", extra={"outbox_event_id": event.id})
        session.refresh(event)
        last_error = _document_processing_public_error(exc)
        error_code = _document_error_code(exc, filename=document.filename if document else None)
        public_error = _document_public_error(error_code)
        max_attempts = _document_processing_max_attempts()
        document = session.get(Document, document_id)
        if event.attempts >= max_attempts:
            last_error = f"document processing failed after {event.attempts} attempts: {last_error}"
            event.status = "failed"
            if document is not None:
                document.parse_status = "failed"
                document.parse_error_code = DOCUMENT_ERROR_ATTEMPTS_EXHAUSTED
                document.parse_error = _document_public_error(DOCUMENT_ERROR_ATTEMPTS_EXHAUSTED)
                document.processing_completed_at = _utcnow()
        else:
            event.status = "pending"
            event.available_at = _next_document_processing_available_at(event.attempts)
            if document is not None:
                document.parse_status = "pending"
                document.parse_error_code = error_code
                document.parse_error = public_error
                document.processing_completed_at = None
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
    previously_successful = document.parse_status == "success" and session.scalar(
        select(DocumentChunk.id).where(DocumentChunk.document_id == document_id).limit(1)
    ) is not None
    previous_processing_completed_at = document.processing_completed_at
    document.parse_status = "processing"
    document.processing_started_at = _utcnow()
    document.processing_completed_at = None
    document.parse_error = None
    document.parse_error_code = None
    session.flush()
    if content_bytes is None:
        try:
            storage = object_storage or build_document_object_storage()
            content_bytes = storage.get_bytes(document.object_key)
        except ObjectStorageUnavailable as exc:
            document.parse_status = "success" if previously_successful else "pending"
            document.parse_error_code = (
                None if previously_successful else DOCUMENT_ERROR_OBJECT_STORAGE_UNAVAILABLE
            )
            document.parse_error = (
                None
                if previously_successful
                else _document_public_error(DOCUMENT_ERROR_OBJECT_STORAGE_UNAVAILABLE)
            )
            document.processing_completed_at = (
                previous_processing_completed_at if previously_successful else None
            )
            session.flush()
            raise DocumentProcessingUnavailable("document object storage is unavailable") from exc
    try:
        parsed_content = _parse_document_content(
            content_bytes,
            filename=document.filename,
            mime_type=document.mime_type,
            ocr_client=ocr_client,
            document_id=document.id,
        )
        parsed_chunks = parsed_content.chunks
        chunk_records = _build_document_chunk_records(document=document, parsed_chunks=parsed_chunks)
        document._prepared_document_chunk_records = chunk_records
        session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        _store_document_chunks(session, document=document, parsed_chunks=parsed_chunks)
        document.parse_status = "success"
        document.parse_error = None
        document.parse_error_code = None
        document.page_count = parsed_content.page_count
        document.block_count = parsed_content.block_count
        document.parser_version = parsed_content.parser_version
        document.processing_completed_at = _utcnow()
        session.flush()
        return {"document_id": document.id, "status": "success", "chunk_count": len(parsed_chunks)}
    except EmbeddingUnavailable as exc:
        document.parse_status = "success" if previously_successful else "pending"
        document.parse_error_code = None if previously_successful else DOCUMENT_ERROR_EMBEDDING_UNAVAILABLE
        document.parse_error = (
            None
            if previously_successful
            else _document_public_error(DOCUMENT_ERROR_EMBEDDING_UNAVAILABLE)
        )
        document.processing_completed_at = (
            previous_processing_completed_at if previously_successful else None
        )
        session.flush()
        raise DocumentProcessingUnavailable(str(exc)) from exc
    except (ValueError, DocumentParsingError) as exc:
        document.parse_status = "success" if previously_successful else "failed"
        if previously_successful:
            document.parse_error_code = None
            document.parse_error = None
            document.processing_completed_at = previous_processing_completed_at
        else:
            document.parse_error_code = _document_error_code(exc, filename=document.filename)
            document.parse_error = _document_safe_parse_error(exc, document.parse_error_code)
            document.processing_completed_at = _utcnow()
        session.flush()
        return {"document_id": document.id, "status": "failed", "chunk_count": 0, "parse_error": document.parse_error}

def _parse_document_content(
    content_bytes: bytes,
    *,
    filename: str,
    mime_type: str,
    ocr_client: OCRClient | None = None,
    document_id: str | None = None,
) -> ParsedDocumentContent:
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
        normalized = normalize_chunk_text(content)
        if not normalized:
            raise ValueError("document contains no text")
        _validate_extracted_text_limit(len(normalized))
        chunk_type = (
            ChunkType.MARKDOWN
            if normalized_type in {"text/markdown", "application/markdown"}
            or suffix in {".md", ".markdown"}
            else ChunkType.TEXT
        )
        drafts = _CHUNKER_REGISTRY.chunk(chunk_type, normalized)
        parsed_chunks = _build_structured_chunk_payloads(
            drafts,
            document_id=_chunk_document_id(
                document_id=document_id,
                filename=filename,
                normalized_content=normalized,
            ),
            base_metadata={"source_type": chunk_type.value},
        )
        _validate_chunk_count(len(parsed_chunks))
        return ParsedDocumentContent(
            chunks=parsed_chunks,
            page_count=1,
            block_count=len(parsed_chunks),
            parser_version=_document_parser_version(),
        )
    supported_suffixes = {".pdf", ".pptx", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
    if suffix not in supported_suffixes and not normalized_type.startswith("image/") and normalized_type != "application/pdf":
        raise ValueError(f"unsupported document mime type: {mime_type}")
    parser = DocumentParser(ocr_service=_LegacyOCRService(ocr_client) if ocr_client else None)
    try:
        result = asyncio.run(parser.parse_document(content=content_bytes, filename=filename, mime_type=mime_type))
    except DocumentParsingError:
        raise
    if result.status is ParseStatus.FAILED:
        if suffix == ".pdf":
            raise ValueError("pdf document contains no extractable text")
        if normalized_type.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}:
            raise ValueError("image OCR produced no text")
        raise ValueError("document parser produced no text")
    drafts: list[ChunkDraft] = []
    extracted_chars = 0
    for block in result.blocks:
        normalized = normalize_chunk_text(block.text)
        if not normalized:
            continue
        extracted_chars += len(normalized)
        _validate_extracted_text_limit(extracted_chars)
        metadata = block.model_dump(mode="json")
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
        drafts.extend(
            _CHUNKER_REGISTRY.chunk(
                _chunk_type_for_document_block(block.file_type.value),
                normalized,
                metadata=metadata,
            )
        )
    parsed = _build_structured_chunk_payloads(
        drafts,
        document_id=_chunk_document_id(
            document_id=document_id,
            filename=filename,
            normalized_content=result.content_sha256,
        ),
    )
    _validate_chunk_count(len(parsed))
    if not parsed:
        raise ValueError("document parser produced no text")
    return ParsedDocumentContent(
        chunks=parsed,
        page_count=result.page_count,
        block_count=result.block_count,
        parser_version=result.parser_version.strip() or _document_parser_version(),
    )


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
    return normalize_chunk_text(content)


def _build_structured_chunk_payloads(
    drafts: list[ChunkDraft],
    *,
    document_id: str,
    base_metadata: dict | None = None,
) -> list[dict]:
    chunks = _CHUNK_METADATA_BUILDER.build(
        drafts,
        document_id=document_id,
        base_metadata=base_metadata,
    )
    return [{"content": chunk.content, "metadata": dict(chunk.metadata)} for chunk in chunks]


def _chunk_document_id(*, document_id: str | None, filename: str, normalized_content: str) -> str:
    if document_id:
        return document_id
    digest = sha256(normalized_content.encode("utf-8")).hexdigest()
    return f"unpersisted:{filename}:{digest}"


def _chunk_type_for_document_block(file_type: str) -> ChunkType:
    if file_type == "pptx":
        return ChunkType.SLIDE
    if file_type == "image":
        return ChunkType.IMAGE_DESCRIPTION
    return ChunkType.TEXT

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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _document_parser_version() -> str:
    configured = os.getenv("DOCUMENT_PARSER_VERSION", "document-parser-v2").strip()
    return configured or "document-parser-v2"


def _exception_chain_contains(exc: Exception, expected_type: type[BaseException]) -> bool:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, expected_type):
            return True
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _document_error_code(exc: Exception, *, filename: str | None = None) -> str:
    if _exception_chain_contains(exc, EmbeddingUnavailable):
        return DOCUMENT_ERROR_EMBEDDING_UNAVAILABLE
    if _exception_chain_contains(exc, ObjectStorageUnavailable):
        return DOCUMENT_ERROR_OBJECT_STORAGE_UNAVAILABLE
    if isinstance(exc, SQLAlchemyError):
        return DOCUMENT_ERROR_INTERNAL
    if isinstance(exc, DocumentTooLargeError):
        return DOCUMENT_ERROR_FILE_TOO_LARGE
    if isinstance(exc, (UnsupportedDocumentTypeError, FileTypeMismatchError)):
        return DOCUMENT_ERROR_UNSUPPORTED_TYPE
    if isinstance(exc, EncryptedPDFError):
        return DOCUMENT_ERROR_ENCRYPTED_PDF
    message = str(exc).lower()
    if "encrypted pdf" in message:
        return DOCUMENT_ERROR_ENCRYPTED_PDF
    if isinstance(exc, CorruptedDocumentError):
        suffix = Path(filename or "").suffix.lower()
        if suffix == ".pdf":
            return DOCUMENT_ERROR_CORRUPTED_PDF
        if suffix == ".pptx":
            return DOCUMENT_ERROR_CORRUPTED_PPTX

    if "content is required" in message or "empty file" in message:
        return DOCUMENT_ERROR_EMPTY_FILE
    if "image ocr produced no text" in message:
        return DOCUMENT_ERROR_OCR_NO_TEXT
    if "no extractable text" in message or "parser produced no text" in message or "contains no text" in message:
        return DOCUMENT_ERROR_PARSER_NO_TEXT
    if "unsupported" in message or "mime type" in message:
        return DOCUMENT_ERROR_UNSUPPORTED_TYPE
    if "filename" in message:
        return DOCUMENT_ERROR_INVALID_FILENAME
    if "pdf" in message and ("corrupt" in message or "could not be parsed" in message or "could not be opened" in message):
        return DOCUMENT_ERROR_CORRUPTED_PDF
    if "pptx" in message and ("corrupt" in message or "could not be parsed" in message or "could not be opened" in message):
        return DOCUMENT_ERROR_CORRUPTED_PPTX
    if "exceeds" in message or "too large" in message:
        return DOCUMENT_ERROR_FILE_TOO_LARGE
    return DOCUMENT_ERROR_INTERNAL


def _document_public_error(error_code: str) -> str:
    return {
        DOCUMENT_ERROR_EMPTY_FILE: "The document is empty.",
        DOCUMENT_ERROR_FILE_TOO_LARGE: "The document exceeds a processing limit.",
        DOCUMENT_ERROR_UNSUPPORTED_TYPE: "The document type is not supported.",
        DOCUMENT_ERROR_INVALID_FILENAME: "The document filename is invalid.",
        DOCUMENT_ERROR_CORRUPTED_PDF: "The PDF document is corrupted.",
        DOCUMENT_ERROR_ENCRYPTED_PDF: "Encrypted PDF documents are not supported.",
        DOCUMENT_ERROR_CORRUPTED_PPTX: "The PowerPoint document is corrupted.",
        DOCUMENT_ERROR_OCR_NO_TEXT: "No readable text was found in the image.",
        DOCUMENT_ERROR_PARSER_NO_TEXT: "No readable text was found in the document.",
        DOCUMENT_ERROR_OBJECT_STORAGE_UNAVAILABLE: "Document storage is temporarily unavailable. Processing will retry automatically.",
        DOCUMENT_ERROR_EMBEDDING_UNAVAILABLE: "Document processing is temporarily unavailable. Processing will retry automatically.",
        DOCUMENT_ERROR_ATTEMPTS_EXHAUSTED: "Document processing could not be completed after multiple attempts.",
        DOCUMENT_ERROR_INTERNAL: "Document processing failed. Please try again later.",
    }.get(error_code, "Document processing failed. Please try again later.")


def _document_safe_parse_error(exc: Exception, error_code: str) -> str:
    if error_code in {
        DOCUMENT_ERROR_EMPTY_FILE,
        DOCUMENT_ERROR_FILE_TOO_LARGE,
        DOCUMENT_ERROR_UNSUPPORTED_TYPE,
        DOCUMENT_ERROR_INVALID_FILENAME,
        DOCUMENT_ERROR_CORRUPTED_PDF,
        DOCUMENT_ERROR_ENCRYPTED_PDF,
        DOCUMENT_ERROR_CORRUPTED_PPTX,
        DOCUMENT_ERROR_OCR_NO_TEXT,
        DOCUMENT_ERROR_PARSER_NO_TEXT,
    }:
        return str(exc)
    return _document_public_error(error_code)

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
                id=metadata.get("chunk_id") or f"chunk-{uuid4()}",
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
    documents = session.scalars(
        select(Document)
        .where(Document.owner_user_id == user_id)
        .order_by(Document.created_at.desc(), Document.id.desc())
    ).all()
    return [_document_to_dict(document) for document in documents]


def get_document_record(session: Session, *, user_id: str, document_id: str) -> dict | None:
    document = session.scalar(
        select(Document).where(Document.id == document_id, Document.owner_user_id == user_id)
    )
    return _document_to_dict(document) if document is not None else None
