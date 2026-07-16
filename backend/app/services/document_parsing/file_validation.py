from __future__ import annotations

import hashlib
import io
import os
import zipfile
from dataclasses import dataclass

from PIL import Image

from .exceptions import CorruptedDocumentError, DocumentTooLargeError, FileTypeMismatchError, UnsupportedDocumentTypeError
from .models import DocumentFileType


IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/bmp", "image/tiff"}
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@dataclass(frozen=True)
class ValidatedDocument:
    file_type: DocumentFileType
    mime_type: str
    sha256: str


def validate_document(*, content: bytes, filename: str, mime_type: str | None) -> ValidatedDocument:
    max_bytes = _int_env("DOCUMENT_MAX_FILE_SIZE_BYTES", _int_env("DOCUMENT_MAX_UPLOAD_BYTES", 20 * 1024 * 1024))
    if not content:
        raise CorruptedDocumentError("document content is required")
    if len(content) > max_bytes:
        raise DocumentTooLargeError(f"document exceeds {max_bytes} byte limit")
    detected_type, detected_mime = _detect_type(content)
    supplied = (mime_type or "").strip().lower()
    if supplied and supplied not in {"application/octet-stream", detected_mime}:
        raise FileTypeMismatchError(f"mime type does not match {detected_type.value} content")
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    expected_suffixes = {
        DocumentFileType.PDF: {"pdf"},
        DocumentFileType.PPTX: {"pptx"},
        DocumentFileType.IMAGE: {"png", "jpg", "jpeg", "webp", "bmp", "tiff"},
    }[detected_type]
    if suffix and suffix not in expected_suffixes:
        raise FileTypeMismatchError(f"filename extension does not match {detected_type.value} content")
    if detected_type is DocumentFileType.IMAGE:
        _validate_image(content)
    if detected_type is DocumentFileType.PPTX:
        _validate_pptx_archive(content)
    return ValidatedDocument(detected_type, detected_mime, hashlib.sha256(content).hexdigest())


def _detect_type(content: bytes) -> tuple[DocumentFileType, str]:
    if content.startswith(b"%PDF-"):
        return DocumentFileType.PDF, "application/pdf"
    if content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile as exc:
            raise CorruptedDocumentError("invalid zip document") from exc
        if "[Content_Types].xml" in names and "ppt/presentation.xml" in names:
            return DocumentFileType.PPTX, PPTX_MIME
    try:
        with Image.open(io.BytesIO(content)) as image:
            detected_mime = Image.MIME.get(image.format, "").lower()
    except Exception as exc:
        raise UnsupportedDocumentTypeError("unsupported or corrupted document type") from exc
    if detected_mime not in IMAGE_MIME_TYPES:
        raise UnsupportedDocumentTypeError("unsupported image type")
    return DocumentFileType.IMAGE, detected_mime


def _validate_image(content: bytes) -> None:
    try:
        with Image.open(io.BytesIO(content)) as image:
            pixels = image.width * image.height
    except Exception as exc:
        raise CorruptedDocumentError("image document could not be inspected") from exc
    if pixels > _int_env("DOCUMENT_MAX_IMAGE_PIXELS", 40_000_000):
        raise DocumentTooLargeError(f"image document exceeds {_int_env('DOCUMENT_MAX_IMAGE_PIXELS', 40_000_000)} pixel limit")


def _validate_pptx_archive(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            total_size = 0
            for item in archive.infolist():
                if item.filename.startswith("/") or ".." in item.filename.replace("\\", "/").split("/"):
                    raise CorruptedDocumentError("pptx archive contains unsafe path")
                total_size += item.file_size
                if item.compress_size and item.file_size / item.compress_size > 100:
                    raise CorruptedDocumentError("pptx archive compression ratio is unsafe")
            if total_size > _int_env("DOCUMENT_MAX_PPT_UNCOMPRESSED_BYTES", 200 * 1024 * 1024):
                raise DocumentTooLargeError("pptx archive uncompressed size exceeds limit")
    except zipfile.BadZipFile as exc:
        raise CorruptedDocumentError("invalid pptx archive") from exc


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
