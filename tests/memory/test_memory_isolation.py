from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import pytest

from backend.app.domain.memory import MemoryNotFound, MemoryScopeNotFound
from backend.app.infrastructure.persistence.repositories.memory_repository import (
    SQLAlchemyMemoryRepository,
)
from backend.app.models import Memory
from tests.memory.helpers import FIXED_NOW, add_memory_scope, mastery_command, preference_command


def _add_isolation_scopes(session: Session) -> None:
    add_memory_scope(session, user_id="user-a", goal_id="goal-a")
    add_memory_scope(session, user_id="user-a", goal_id="goal-a-other")
    add_memory_scope(session, user_id="user-b", goal_id="goal-b")


def test_get_by_id_never_exposes_another_users_memory(db_session: Session) -> None:
    _add_isolation_scopes(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    other_users_memory = repository.create_or_get(
        preference_command(user_id="user-b", idempotency_key="private-memory"),
        now=FIXED_NOW,
    )

    assert (
        repository.get_by_id(
            user_id="user-a",
            memory_id=other_users_memory.id,
            now=FIXED_NOW,
        )
        is None
    )
    assert (
        repository.get_by_id(
            user_id="user-a",
            memory_id=other_users_memory.id,
            include_inactive=True,
            now=FIXED_NOW,
        )
        is None
    )


def test_disable_another_users_memory_raises_generic_non_leaking_error(
    db_session: Session,
) -> None:
    _add_isolation_scopes(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    other_users_memory = repository.create_or_get(
        preference_command(user_id="user-b", idempotency_key="private-disable"),
        now=FIXED_NOW,
    )

    with pytest.raises(MemoryNotFound) as error:
        repository.disable(
            user_id="user-a",
            memory_id=other_users_memory.id,
            reason="incorrect",
            now=FIXED_NOW,
        )

    message = str(error.value)
    assert message == "Memory was not found."
    assert "user-b" not in message
    assert other_users_memory.id not in message


def test_foreign_goal_create_raises_generic_non_leaking_scope_error(
    db_session: Session,
) -> None:
    _add_isolation_scopes(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)

    with pytest.raises(MemoryScopeNotFound) as error:
        repository.create_or_get(
            mastery_command(
                user_id="user-a",
                goal_id="goal-b",
                idempotency_key="foreign-goal",
            ),
            now=FIXED_NOW,
        )

    message = str(error.value)
    assert message == "Memory scope was not found."
    assert "user-b" not in message
    assert "goal-b" not in message


def test_goal_listing_is_scoped_and_user_scope_option_is_exact(db_session: Session) -> None:
    _add_isolation_scopes(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    user_memory = repository.create_or_get(
        preference_command(user_id="user-a", idempotency_key="user-scope"),
        now=FIXED_NOW,
    )
    goal_a_memory = repository.create_or_get(
        mastery_command(user_id="user-a", goal_id="goal-a", idempotency_key="goal-a-scope"),
        now=FIXED_NOW,
    )
    other_a_goal_memory = repository.create_or_get(
        mastery_command(
            user_id="user-a",
            goal_id="goal-a-other",
            idempotency_key="goal-a-other-scope",
        ),
        now=FIXED_NOW,
    )
    goal_b_memory = repository.create_or_get(
        mastery_command(user_id="user-b", goal_id="goal-b", idempotency_key="goal-b-scope"),
        now=FIXED_NOW,
    )

    combined = repository.list_active(
        user_id="user-a",
        goal_id="goal-a",
        now=FIXED_NOW,
    )
    goal_only = repository.list_active(
        user_id="user-a",
        goal_id="goal-a",
        include_user_scope=False,
        now=FIXED_NOW,
    )
    user_only = repository.list_active(user_id="user-a", now=FIXED_NOW)

    assert {record.id for record in combined} == {user_memory.id, goal_a_memory.id}
    assert [record.id for record in goal_only] == [goal_a_memory.id]
    assert [record.id for record in user_only] == [user_memory.id]
    assert other_a_goal_memory.id not in {record.id for record in combined}
    assert goal_b_memory.id not in {record.id for record in combined}


def test_database_rejects_cross_user_goal_pairing(db_session: Session) -> None:
    _add_isolation_scopes(db_session)
    db_session.add(
        Memory(
            id="memory-cross-user-goal",
            user_id="user-a",
            goal_id="goal-b",
            memory_type="mastery_summary",
            schema_version="memory-v1",
            content_json={"knowledge_node_id": "python-basics"},
            content_hash="cross-user-hash",
            source_kind="mastery_record",
            source_ref_id="cross-user-source",
            source_metadata={},
            importance=0.5,
            confidence=0.8,
            is_enabled=True,
            expires_at=None,
            disabled_at=None,
            disabled_reason=None,
            idempotency_key="cross-user-goal",
            created_at=FIXED_NOW,
            updated_at=FIXED_NOW,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_idempotency_key_is_isolated_per_user(db_session: Session) -> None:
    _add_isolation_scopes(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    shared_key = "shared-across-users"

    user_a_memory = repository.create_or_get(
        preference_command(user_id="user-a", idempotency_key=shared_key),
        now=FIXED_NOW,
    )
    user_b_memory = repository.create_or_get(
        preference_command(user_id="user-b", idempotency_key=shared_key),
        now=FIXED_NOW,
    )

    assert user_a_memory.id != user_b_memory.id
    assert (
        db_session.scalar(
            select(func.count()).select_from(Memory).where(Memory.idempotency_key == shared_key)
        )
        == 2
    )
