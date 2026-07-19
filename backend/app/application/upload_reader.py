from __future__ import annotations

import os

from fastapi import UploadFile

from backend.app.services.document_parsing.exceptions import CorruptedDocumentError, DocumentTooLargeError


DEFAULT_DOCUMENT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_UPLOAD_CHUNK_SIZE = 1024 * 1024


def document_max_upload_bytes() -> int:
    try:
        configured = int(
            os.getenv("DOCUMENT_MAX_UPLOAD_BYTES", str(DEFAULT_DOCUMENT_MAX_UPLOAD_BYTES))
        )
    except ValueError:
        return DEFAULT_DOCUMENT_MAX_UPLOAD_BYTES
    return configured if configured > 0 else DEFAULT_DOCUMENT_MAX_UPLOAD_BYTES


async def read_upload_limited(
    upload: UploadFile,
    *,
    max_bytes: int,
    chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise DocumentTooLargeError(
                    f"document upload exceeds {max_bytes} byte limit"
                )
            chunks.append(chunk)
    finally:
        await upload.close()

    if total == 0:
        raise CorruptedDocumentError("document content is required")
    return b"".join(chunks)
