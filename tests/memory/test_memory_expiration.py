from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from backend.app.infrastructure.persistence.repositories.memory_repository import (
    SQLAlchemyMemoryRepository,
)
from backend.app.models import Memory
from tests.memory.helpers import FIXED_NOW, add_memory_scope, preference_command


def _create_with_expiry(
    session: Session,
    *,
    idempotency_key: str,
    expires_at,
):
    return SQLAlchemyMemoryRepository(session).create_or_get(
        preference_command(idempotency_key=idempotency_key, expires_at=expires_at),
        now=FIXED_NOW - timedelta(days=2),
    )


def test_no_expiry_is_active(db_session: Session) -> None:
    add_memory_scope(db_session)
    created = _create_with_expiry(db_session, idempotency_key="expiry-none", expires_at=None)

    records = SQLAlchemyMemoryRepository(db_session).list_active(user_id="test-user", now=FIXED_NOW)

    assert [record.id for record in records] == [created.id]


def test_expiry_after_now_is_active(db_session: Session) -> None:
    add_memory_scope(db_session)
    created = _create_with_expiry(
        db_session,
        idempotency_key="expiry-future",
        expires_at=FIXED_NOW + timedelta(seconds=1),
    )

    records = SQLAlchemyMemoryRepository(db_session).list_active(user_id="test-user", now=FIXED_NOW)

    assert [record.id for record in records] == [created.id]


def test_expiry_equal_to_now_is_inactive(db_session: Session) -> None:
    add_memory_scope(db_session)
    _create_with_expiry(db_session, idempotency_key="expiry-equal", expires_at=FIXED_NOW)

    assert SQLAlchemyMemoryRepository(db_session).list_active(user_id="test-user", now=FIXED_NOW) == []


def test_expiry_before_now_is_inactive(db_session: Session) -> None:
    add_memory_scope(db_session)
    _create_with_expiry(
        db_session,
        idempotency_key="expiry-before",
        expires_at=FIXED_NOW - timedelta(seconds=1),
    )

    assert SQLAlchemyMemoryRepository(db_session).list_active(user_id="test-user", now=FIXED_NOW) == []


def test_expired_row_is_hidden_by_id_by_default(db_session: Session) -> None:
    add_memory_scope(db_session)
    created = _create_with_expiry(db_session, idempotency_key="expiry-hidden", expires_at=FIXED_NOW)

    fetched = SQLAlchemyMemoryRepository(db_session).get_by_id(
        user_id="test-user",
        memory_id=created.id,
        now=FIXED_NOW,
    )

    assert fetched is None


def test_include_inactive_fetches_expired_row_by_id(db_session: Session) -> None:
    add_memory_scope(db_session)
    created = _create_with_expiry(db_session, idempotency_key="expiry-included", expires_at=FIXED_NOW)

    fetched = SQLAlchemyMemoryRepository(db_session).get_by_id(
        user_id="test-user",
        memory_id=created.id,
        include_inactive=True,
        now=FIXED_NOW,
    )

    assert fetched == created


def test_disabled_row_is_hidden_even_without_expiry(db_session: Session) -> None:
    add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    created = _create_with_expiry(db_session, idempotency_key="disabled-no-expiry", expires_at=None)
    repository.disable(user_id="test-user", memory_id=created.id, reason="incorrect", now=FIXED_NOW)

    assert repository.list_active(user_id="test-user", now=FIXED_NOW) == []
    assert repository.get_by_id(user_id="test-user", memory_id=created.id, now=FIXED_NOW) is None


def test_disabled_unexpired_row_is_hidden(db_session: Session) -> None:
    add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    created = _create_with_expiry(
        db_session,
        idempotency_key="disabled-unexpired",
        expires_at=FIXED_NOW + timedelta(days=1),
    )
    repository.disable(user_id="test-user", memory_id=created.id, reason="incorrect", now=FIXED_NOW)

    assert repository.list_active(user_id="test-user", now=FIXED_NOW) == []


def test_expiry_does_not_change_is_enabled(db_session: Session) -> None:
    add_memory_scope(db_session)
    created = _create_with_expiry(db_session, idempotency_key="expiry-keeps-enabled", expires_at=FIXED_NOW)

    SQLAlchemyMemoryRepository(db_session).list_active(user_id="test-user", now=FIXED_NOW)
    stored = db_session.get(Memory, created.id)

    assert stored is not None
    assert stored.is_enabled is True
