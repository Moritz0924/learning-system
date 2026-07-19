from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.domain.memory import (
    MemoryIdempotencyConflict,
    MemoryNotFound,
    MemoryRecord,
    MemoryScopeNotFound,
    UnsupportedMemoryType,
)
from backend.app.infrastructure.persistence.repositories.memory_repository import (
    SQLAlchemyMemoryRepository,
)
from backend.app.models import Memory
from tests.memory.helpers import (
    FIXED_NOW,
    add_memory_scope,
    mastery_command,
    preference_command,
)


def test_create_or_get_creates_user_and_goal_scope_records(db_session: Session) -> None:
    add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)

    user_memory = repository.create_or_get(preference_command(), now=FIXED_NOW)
    goal_memory = repository.create_or_get(mastery_command(), now=FIXED_NOW)

    assert isinstance(user_memory, MemoryRecord)
    assert not isinstance(user_memory, Memory)
    assert user_memory.goal_id is None
    assert goal_memory.goal_id == "test-goal"
    assert user_memory.created_at == FIXED_NOW
    assert goal_memory.updated_at == FIXED_NOW


def test_create_or_get_uses_stable_content_hash_independent_of_key_order(db_session: Session) -> None:
    add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    first = preference_command(
        content={"preference_key": "explanation_style", "preference_value": ["examples", "diagrams"]}
    )
    reordered = preference_command(
        content={"preference_value": ["examples", "diagrams"], "preference_key": "explanation_style"}
    )

    created = repository.create_or_get(first, now=FIXED_NOW)
    fetched = repository.create_or_get(reordered, now=FIXED_NOW)

    assert fetched.id == created.id
    assert fetched.content_hash == created.content_hash


def test_list_active_orders_exactly_and_filters_memory_types(db_session: Session) -> None:
    add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    commands = [
        preference_command(idempotency_key="order-low", importance=0.2),
        preference_command(idempotency_key="order-old", importance=0.8),
        preference_command(idempotency_key="order-new", importance=0.8),
        preference_command(
            memory_type="long_term_goal",
            content={"title": "Ship tutor", "target_outcome": "A reliable tutor"},
            idempotency_key="order-type",
            importance=1.0,
        ),
    ]
    created = [
        repository.create_or_get(command, now=FIXED_NOW + timedelta(minutes=index))
        for index, command in enumerate(commands)
    ]

    all_records = repository.list_active(user_id="test-user", now=FIXED_NOW + timedelta(hours=1))
    preferences = repository.list_active(
        user_id="test-user",
        memory_types={"learning_preference"},
        now=FIXED_NOW + timedelta(hours=1),
    )

    assert [record.id for record in all_records] == [created[3].id, created[2].id, created[1].id, created[0].id]
    assert [record.id for record in preferences] == [created[2].id, created[1].id, created[0].id]


def test_list_active_orders_equal_importance_and_creation_by_id(db_session: Session, monkeypatch) -> None:
    add_memory_scope(db_session)
    generated_ids = iter(["memory-b", "memory-a"])
    monkeypatch.setattr(
        "backend.app.infrastructure.persistence.repositories.memory_repository.uuid4",
        lambda: next(generated_ids),
    )
    repository = SQLAlchemyMemoryRepository(db_session)
    repository.create_or_get(preference_command(idempotency_key="tie-b"), now=FIXED_NOW)
    repository.create_or_get(preference_command(idempotency_key="tie-a"), now=FIXED_NOW)

    records = repository.list_active(user_id="test-user", now=FIXED_NOW)

    assert [record.id for record in records] == ["memory-memory-a", "memory-memory-b"]


def test_list_active_enforces_goal_scope_options(db_session: Session) -> None:
    add_memory_scope(db_session)
    add_memory_scope(db_session, user_id="other-user", goal_id="other-goal")
    repository = SQLAlchemyMemoryRepository(db_session)
    user_record = repository.create_or_get(preference_command(), now=FIXED_NOW)
    goal_record = repository.create_or_get(mastery_command(), now=FIXED_NOW)
    other_goal_record = repository.create_or_get(
        mastery_command(
            user_id="other-user",
            goal_id="other-goal",
            idempotency_key="other-goal-memory",
        ),
        now=FIXED_NOW,
    )

    user_scope = repository.list_active(user_id="test-user", now=FIXED_NOW)
    combined = repository.list_active(user_id="test-user", goal_id="test-goal", now=FIXED_NOW)
    goal_only = repository.list_active(
        user_id="test-user",
        goal_id="test-goal",
        include_user_scope=False,
        now=FIXED_NOW,
    )

    assert [record.id for record in user_scope] == [user_record.id]
    assert {record.id for record in combined} == {user_record.id, goal_record.id}
    assert [record.id for record in goal_only] == [goal_record.id]
    assert other_goal_record.id not in {record.id for record in combined}


@pytest.mark.parametrize("limit", [1, 50, 100])
def test_list_active_accepts_valid_limits(db_session: Session, limit: int) -> None:
    add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    repository.create_or_get(preference_command(), now=FIXED_NOW)

    assert len(repository.list_active(user_id="test-user", limit=limit, now=FIXED_NOW)) == 1


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_list_active_rejects_invalid_limits(db_session: Session, limit: int) -> None:
    repository = SQLAlchemyMemoryRepository(db_session)

    with pytest.raises(ValueError):
        repository.list_active(user_id="test-user", limit=limit, now=FIXED_NOW)


def test_list_active_returns_empty_for_explicit_empty_memory_types(db_session: Session) -> None:
    add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    repository.create_or_get(preference_command(), now=FIXED_NOW)

    assert repository.list_active(user_id="test-user", memory_types=set(), now=FIXED_NOW) == []


def test_list_active_rejects_unsupported_runtime_memory_type(db_session: Session) -> None:
    repository = SQLAlchemyMemoryRepository(db_session)

    with pytest.raises(UnsupportedMemoryType):
        repository.list_active(
            user_id="test-user",
            memory_types={"conversation_summary"},  # type: ignore[arg-type]
            now=FIXED_NOW,
        )


def test_disable_writes_exact_fields_and_preserves_payload(db_session: Session) -> None:
    add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    created = repository.create_or_get(
        preference_command(source_metadata={"origin": "settings"}),
        now=FIXED_NOW,
    )
    disabled_now = FIXED_NOW + timedelta(hours=1)

    disabled = repository.disable(
        user_id="test-user",
        memory_id=created.id,
        reason="  user_revoked  ",
        now=disabled_now,
    )

    assert disabled.is_enabled is False
    assert disabled.disabled_at == disabled_now
    assert disabled.disabled_reason == "user_revoked"
    assert disabled.updated_at == disabled_now
    assert disabled.content == created.content
    assert disabled.content_hash == created.content_hash
    assert disabled.source_kind == created.source_kind
    assert disabled.source_ref_id == created.source_ref_id
    assert disabled.source_metadata == created.source_metadata


def test_repeat_disable_keeps_first_reason_and_time(db_session: Session) -> None:
    add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    created = repository.create_or_get(preference_command(), now=FIXED_NOW)
    first = repository.disable(
        user_id="test-user",
        memory_id=created.id,
        reason="incorrect",
        now=FIXED_NOW + timedelta(minutes=1),
    )

    repeated = repository.disable(
        user_id="test-user",
        memory_id=created.id,
        reason="privacy_request",
        now=FIXED_NOW + timedelta(minutes=2),
    )

    assert repeated.disabled_at == first.disabled_at
    assert repeated.disabled_reason == first.disabled_reason
    assert repeated.updated_at == first.updated_at
    assert repeated.content == first.content


@pytest.mark.parametrize("reason", ["", "unknown", "x" * 129])
def test_disable_rejects_invalid_reason_without_echoing_it(db_session: Session, reason: str) -> None:
    repository = SQLAlchemyMemoryRepository(db_session)

    with pytest.raises(ValueError) as error:
        repository.disable(user_id="test-user", memory_id="memory-1", reason=reason, now=FIXED_NOW)

    assert reason not in str(error.value) or not reason


def test_disable_missing_record_raises_generic_error(db_session: Session) -> None:
    add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)

    with pytest.raises(MemoryNotFound) as error:
        repository.disable(
            user_id="test-user",
            memory_id="sensitive-memory-id",
            reason="incorrect",
            now=FIXED_NOW,
        )

    assert "sensitive-memory-id" not in str(error.value)


def test_repository_does_not_commit(db_session: Session, monkeypatch) -> None:
    add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)

    def unexpected_commit() -> None:
        pytest.fail("repository must not commit")

    monkeypatch.setattr(db_session, "commit", unexpected_commit)
    repository.create_or_get(preference_command(), now=FIXED_NOW)


def test_caller_rollback_makes_created_row_disappear(db_session: Session) -> None:
    add_memory_scope(db_session)
    db_session.commit()
    repository = SQLAlchemyMemoryRepository(db_session)
    repository.create_or_get(preference_command(), now=FIXED_NOW)

    db_session.rollback()

    assert db_session.scalar(select(func.count()).select_from(Memory)) == 0


def test_caller_commit_allows_new_session_to_read(session_factory) -> None:
    with session_factory() as writer:
        add_memory_scope(writer)
        writer.commit()
        repository = SQLAlchemyMemoryRepository(writer)
        created = repository.create_or_get(preference_command(), now=FIXED_NOW)
        writer.commit()

    with session_factory() as reader:
        fetched = SQLAlchemyMemoryRepository(reader).get_by_id(
            user_id="test-user",
            memory_id=created.id,
            now=FIXED_NOW,
        )

    assert fetched == created


def test_missing_user_and_foreign_goal_use_same_generic_scope_error(db_session: Session) -> None:
    add_memory_scope(db_session)
    add_memory_scope(db_session, user_id="other-user", goal_id="foreign-sensitive-goal")
    repository = SQLAlchemyMemoryRepository(db_session)
    errors: list[MemoryScopeNotFound] = []

    for command in (
        preference_command(user_id="missing-sensitive-user"),
        mastery_command(goal_id="foreign-sensitive-goal"),
    ):
        with pytest.raises(MemoryScopeNotFound) as error:
            repository.create_or_get(command, now=FIXED_NOW)
        errors.append(error.value)

    assert str(errors[0]) == str(errors[1])
    assert "missing-sensitive-user" not in str(errors[0])
    assert "foreign-sensitive-goal" not in str(errors[1])


def test_same_key_same_command_returns_same_id_and_mismatch_conflicts(db_session: Session) -> None:
    add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    command = preference_command()
    created = repository.create_or_get(command, now=FIXED_NOW)

    fetched = repository.create_or_get(command, now=FIXED_NOW + timedelta(minutes=1))

    assert fetched.id == created.id
    with pytest.raises(MemoryIdempotencyConflict):
        repository.create_or_get(
            preference_command(
                content={"preference_key": "explanation_style", "preference_value": "concise"}
            ),
            now=FIXED_NOW + timedelta(minutes=1),
        )
