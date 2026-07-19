from __future__ import annotations

import pytest
from sqlalchemy.exc import SQLAlchemyError

from backend.app.application.document_service import create_document_record


class TrackingStorage:
    def __init__(self) -> None:
        self.stored_keys: list[str] = []
        self.deleted_keys: list[str] = []

    def put_bytes(self, object_key: str, content: bytes, *, content_type: str) -> None:
        self.stored_keys.append(object_key)

    def delete_bytes(self, object_key: str) -> None:
        self.deleted_keys.append(object_key)


def test_document_creation_rolls_back_and_deletes_object_after_database_failure(
    db_session, monkeypatch
):
    storage = TrackingStorage()

    def fail_flush(*args, **kwargs):
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(db_session, "flush", fail_flush)

    with pytest.raises(SQLAlchemyError, match="database unavailable"):
        create_document_record(
            db_session,
            user_id="user-1",
            filename="notes.txt",
            mime_type="text/plain",
            content="durable content",
            processing_mode="defer",
            object_storage=storage,
        )

    assert len(storage.stored_keys) == 1
    assert storage.deleted_keys == storage.stored_keys
    assert not db_session.new
