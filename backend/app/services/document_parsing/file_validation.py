from __future__ import annotations

import hashlib
import io
import os
import zipfile
from dataclasses import dataclass

from PIL import Image
from pypdf import PdfReader
from pptx import Presentation

from .exceptions import (
    CorruptedDocumentError,
    DocumentTooLargeError,
    FileTypeMismatchError,
    UnsupportedDocumentTypeError,
)
from .models import DocumentFileType


IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/bmp", "image/tiff"}
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
ALLOWED_UPLOAD_SUFFIXES = {
    ".pdf",
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tiff",
    ".md",
    ".txt",
}


@dataclass(frozen=True)
class ValidatedDocument:
    file_type: DocumentFileType
    mime_type: str
    sha256: str


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    mime_type: str


def validate_upload_document(
    *, content: bytes, filename: str | None, mime_type: str | None
) -> ValidatedUpload:
    safe_filename = _validate_upload_filename(filename)
    suffix = os.path.splitext(safe_filename)[1].lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise UnsupportedDocumentTypeError(f"unsupported document extension: {suffix or '<none>'}")

    supplied_mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if suffix in {".md", ".txt"}:
        if content.startswith((b"%PDF-", b"PK\x03\x04", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
            raise FileTypeMismatchError("filename extension does not match binary content")
        allowed_mimes = (
            {"text/markdown", "text/plain", "application/markdown", "application/octet-stream"}
            if suffix == ".md"
            else {"text/plain", "application/octet-stream"}
        )
        if supplied_mime not in allowed_mimes:
            raise FileTypeMismatchError("mime type does not match text content")
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CorruptedDocumentError("text document must be utf-8") from exc
        if not decoded.strip() or "\x00" in decoded:
            raise CorruptedDocumentError("text document contains no valid text")
        return ValidatedUpload(
            filename=safe_filename,
            mime_type="text/markdown" if suffix == ".md" else "text/plain",
        )

    validated = validate_document(
        content=content,
        filename=safe_filename,
        mime_type=supplied_mime,
    )
    return ValidatedUpload(filename=safe_filename, mime_type=validated.mime_type)


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
    if detected_type is DocumentFileType.IMAGE:
        expected_suffixes = {
            "image/png": {"png"},
            "image/jpeg": {"jpg", "jpeg"},
            "image/webp": {"webp"},
            "image/bmp": {"bmp"},
            "image/tiff": {"tiff"},
        }[detected_mime]
    else:
        expected_suffixes = {
            DocumentFileType.PDF: {"pdf"},
            DocumentFileType.PPTX: {"pptx"},
        }[detected_type]
    if suffix and suffix not in expected_suffixes:
        raise FileTypeMismatchError(f"filename extension does not match {detected_type.value} content")
    if detected_type is DocumentFileType.IMAGE:
        _validate_image(content)
    if detected_type is DocumentFileType.PDF:
        _validate_pdf(content)
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
            image.verify()
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
    try:
        Presentation(io.BytesIO(content))
    except Exception as exc:
        raise CorruptedDocumentError("pptx document could not be opened") from exc


def _validate_pdf(content: bytes) -> None:
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise CorruptedDocumentError("encrypted pdf documents are not supported")
        len(reader.pages)
    except CorruptedDocumentError:
        raise
    except Exception as exc:
        raise CorruptedDocumentError("pdf document could not be opened") from exc


def _validate_upload_filename(filename: str | None) -> str:
    normalized = (filename or "").strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or "\x00" in normalized
    ):
        raise ValueError("document filename is invalid")
    return normalized


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
