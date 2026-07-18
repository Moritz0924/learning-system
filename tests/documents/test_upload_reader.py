from __future__ import annotations

import io
import asyncio

import pytest
from fastapi import UploadFile

from backend.app.application.upload_reader import read_upload_limited
from backend.app.services.document_parsing.exceptions import CorruptedDocumentError, DocumentTooLargeError


class TrackingBytesIO(io.BytesIO):
    position_when_closed: int | None = None

    def close(self) -> None:
        self.position_when_closed = self.tell()
        super().close()


def test_upload_reader_stops_after_first_chunk_over_limit_and_closes_file():
    file_object = TrackingBytesIO(b"0123456789abcdef")
    upload = UploadFile(file=file_object, filename="large.txt")

    with pytest.raises(DocumentTooLargeError):
        asyncio.run(read_upload_limited(upload, max_bytes=5, chunk_size=4))

    assert file_object.position_when_closed == 8
    assert file_object.closed


def test_upload_reader_rejects_empty_upload_and_closes_file():
    file_object = io.BytesIO()
    upload = UploadFile(file=file_object, filename="empty.txt")

    with pytest.raises(CorruptedDocumentError):
        asyncio.run(read_upload_limited(upload, max_bytes=5, chunk_size=4))

    assert file_object.closed


def test_upload_reader_returns_content_within_limit_and_closes_file():
    file_object = io.BytesIO(b"12345")
    upload = UploadFile(file=file_object, filename="ok.txt")

    assert asyncio.run(read_upload_limited(upload, max_bytes=5, chunk_size=2)) == b"12345"
    assert file_object.closed
