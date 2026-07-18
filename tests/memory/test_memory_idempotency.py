from __future__ import annotations

import ast
import inspect
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.domain.memory import MemoryIdempotencyConflict
from backend.app.infrastructure.persistence.repositories.memory_repository import (
    SQLAlchemyMemoryRepository,
)
from backend.app.models import Memory, User
from tests.memory.helpers import FIXED_NOW, add_memory_scope, mastery_command, preference_command


def _long_term_goal_command(**overrides):
    data = {
        "memory_type": "long_term_goal",
        "content": {"title": "Ship a tutor", "target_outcome": "A reliable learning system"},
        "idempotency_key": "long-term-goal-key",
    }
    data.update(overrides)
    return preference_command(**data)


def _count_key(session: Session, *, user_id: str, key: str) -> int:
    return session.scalar(
        select(func.count()).select_from(Memory).where(
            Memory.user_id == user_id,
            Memory.idempotency_key == key,
        )
    )


def test_matching_normalized_retry_returns_same_id_and_one_row(db_session: Session) -> None:
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-a")
    repository = SQLAlchemyMemoryRepository(db_session)
    key = "normalized-retry"
    first = preference_command(
        user_id=" user-a ",
        idempotency_key=f" {key} ",
        content={"preference_key": " explanation_style ", "preference_value": " examples "},
        source_metadata={"nested": {"b": 2, "a": 1}},
    )
    equivalent = preference_command(
        user_id="user-a",
        idempotency_key=key,
        content={"preference_value": "examples", "preference_key": "explanation_style"},
        source_metadata={"nested": {"a": 1, "b": 2}},
    )

    created = repository.create_or_get(first, now=FIXED_NOW)
    retried = repository.create_or_get(equivalent, now=FIXED_NOW)

    assert retried.id == created.id
    assert _count_key(db_session, user_id="user-a", key=key) == 1


def test_same_key_with_different_content_conflicts_and_preserves_first_row(
    db_session: Session,
) -> None:
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-a")
    repository = SQLAlchemyMemoryRepository(db_session)
    key = "content-conflict"
    created = repository.create_or_get(
        preference_command(user_id="user-a", idempotency_key=key),
        now=FIXED_NOW,
    )

    with pytest.raises(MemoryIdempotencyConflict):
        repository.create_or_get(
            preference_command(
                user_id="user-a",
                idempotency_key=key,
                content={"preference_key": "explanation_style", "preference_value": "concise"},
            ),
            now=FIXED_NOW,
        )

    stored = db_session.scalar(select(Memory).where(Memory.id == created.id))
    assert stored is not None
    assert stored.content_json == created.content
    assert _count_key(db_session, user_id="user-a", key=key) == 1


def test_same_key_with_different_goal_conflicts(db_session: Session) -> None:
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-a")
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-b")
    repository = SQLAlchemyMemoryRepository(db_session)
    key = "goal-conflict"
    repository.create_or_get(
        _long_term_goal_command(user_id="user-a", goal_id="goal-a", idempotency_key=key),
        now=FIXED_NOW,
    )

    with pytest.raises(MemoryIdempotencyConflict):
        repository.create_or_get(
            _long_term_goal_command(user_id="user-a", goal_id="goal-b", idempotency_key=key),
            now=FIXED_NOW,
        )


def test_same_key_with_different_memory_type_conflicts(db_session: Session) -> None:
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-a")
    repository = SQLAlchemyMemoryRepository(db_session)
    key = "type-conflict"
    repository.create_or_get(
        preference_command(user_id="user-a", idempotency_key=key),
        now=FIXED_NOW,
    )

    with pytest.raises(MemoryIdempotencyConflict):
        repository.create_or_get(
            _long_term_goal_command(user_id="user-a", idempotency_key=key),
            now=FIXED_NOW,
        )


@pytest.mark.parametrize(
    ("first_overrides", "retry_overrides"),
    [
        (
            {"source_kind": "explicit_user", "source_ref_id": "source-1"},
            {"source_kind": "learning_event", "source_ref_id": "source-1"},
        ),
        (
            {"source_kind": "learning_event", "source_ref_id": "source-1"},
            {"source_kind": "learning_event", "source_ref_id": "source-2"},
        ),
        (
            {"source_metadata": {"origin": {"channel": "web"}}},
            {"source_metadata": {"origin": {"channel": "mobile"}}},
        ),
    ],
    ids=["source-kind", "source-ref", "normalized-source-metadata"],
)
def test_same_key_with_different_source_fields_conflicts(
    db_session: Session,
    first_overrides: dict,
    retry_overrides: dict,
) -> None:
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-a")
    repository = SQLAlchemyMemoryRepository(db_session)
    key = "source-conflict"
    repository.create_or_get(
        preference_command(user_id="user-a", idempotency_key=key, **first_overrides),
        now=FIXED_NOW,
    )

    with pytest.raises(MemoryIdempotencyConflict):
        repository.create_or_get(
            preference_command(user_id="user-a", idempotency_key=key, **retry_overrides),
            now=FIXED_NOW,
        )


def test_same_key_with_different_schema_version_conflicts(db_session: Session) -> None:
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-a")
    repository = SQLAlchemyMemoryRepository(db_session)
    key = "schema-conflict"
    repository.create_or_get(
        preference_command(user_id="user-a", idempotency_key=key),
        now=FIXED_NOW,
    )

    with pytest.raises(MemoryIdempotencyConflict):
        repository.create_or_get(
            preference_command(user_id="user-a", idempotency_key=key, schema_version="memory-v2"),
            now=FIXED_NOW,
        )


def test_same_key_with_different_expiry_conflicts(db_session: Session) -> None:
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-a")
    repository = SQLAlchemyMemoryRepository(db_session)
    key = "expiry-conflict"
    repository.create_or_get(
        preference_command(
            user_id="user-a",
            idempotency_key=key,
            expires_at=FIXED_NOW + timedelta(days=1),
        ),
        now=FIXED_NOW,
    )

    with pytest.raises(MemoryIdempotencyConflict):
        repository.create_or_get(
            preference_command(
                user_id="user-a",
                idempotency_key=key,
                expires_at=FIXED_NOW + timedelta(days=2),
            ),
            now=FIXED_NOW,
        )


@pytest.mark.parametrize(
    ("field", "first_value", "retry_value"),
    [("importance", 0.2, 0.8), ("confidence", 0.4, 0.9)],
)
def test_same_key_with_different_scores_conflicts(
    db_session: Session,
    field: str,
    first_value: float,
    retry_value: float,
) -> None:
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-a")
    repository = SQLAlchemyMemoryRepository(db_session)
    key = f"{field}-conflict"
    repository.create_or_get(
        preference_command(user_id="user-a", idempotency_key=key, **{field: first_value}),
        now=FIXED_NOW,
    )

    with pytest.raises(MemoryIdempotencyConflict):
        repository.create_or_get(
            preference_command(user_id="user-a", idempotency_key=key, **{field: retry_value}),
            now=FIXED_NOW,
        )


def test_disabled_row_remains_authoritative_for_idempotency(db_session: Session) -> None:
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-a")
    repository = SQLAlchemyMemoryRepository(db_session)
    key = "disabled-idempotency"
    command = preference_command(user_id="user-a", idempotency_key=key)
    created = repository.create_or_get(command, now=FIXED_NOW)
    disabled = repository.disable(
        user_id="user-a",
        memory_id=created.id,
        reason="user_revoked",
        now=FIXED_NOW + timedelta(minutes=1),
    )

    retried = repository.create_or_get(command, now=FIXED_NOW + timedelta(minutes=2))
    assert retried.id == disabled.id
    assert retried.is_enabled is False
    assert retried.disabled_at == disabled.disabled_at

    with pytest.raises(MemoryIdempotencyConflict):
        repository.create_or_get(
            preference_command(
                user_id="user-a",
                idempotency_key=key,
                content={"preference_key": "explanation_style", "preference_value": "concise"},
            ),
            now=FIXED_NOW + timedelta(minutes=2),
        )

    stored = db_session.scalar(select(Memory).where(Memory.id == created.id))
    assert stored is not None
    assert stored.is_enabled is False
    assert _count_key(db_session, user_id="user-a", key=key) == 1


def test_expired_row_remains_authoritative_for_idempotency(db_session: Session) -> None:
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-a")
    repository = SQLAlchemyMemoryRepository(db_session)
    key = "expired-idempotency"
    expires_at = FIXED_NOW + timedelta(minutes=1)
    command = preference_command(
        user_id="user-a",
        idempotency_key=key,
        expires_at=expires_at,
    )
    created = repository.create_or_get(command, now=FIXED_NOW)

    retried = repository.create_or_get(command, now=FIXED_NOW)
    assert retried.id == created.id
    assert (
        repository.get_by_id(
            user_id="user-a",
            memory_id=created.id,
            now=FIXED_NOW + timedelta(minutes=2),
        )
        is None
    )

    with pytest.raises(MemoryIdempotencyConflict):
        repository.create_or_get(
            preference_command(
                user_id="user-a",
                idempotency_key=key,
                expires_at=expires_at,
                content={"preference_key": "explanation_style", "preference_value": "concise"},
            ),
            now=FIXED_NOW,
        )
    assert _count_key(db_session, user_id="user-a", key=key) == 1


def test_two_sessions_sequential_retry_creates_one_row(session_factory) -> None:
    with session_factory() as setup:
        add_memory_scope(setup, user_id="user-a", goal_id="goal-a")
        setup.commit()

    command = preference_command(user_id="user-a", idempotency_key="two-session-sequential")
    with session_factory() as session_a:
        first = SQLAlchemyMemoryRepository(session_a).create_or_get(command, now=FIXED_NOW)
        session_a.commit()
    with session_factory() as session_b:
        second = SQLAlchemyMemoryRepository(session_b).create_or_get(command, now=FIXED_NOW)
        session_b.commit()

    with session_factory() as reader:
        assert second.id == first.id
        assert _count_key(reader, user_id="user-a", key=command.idempotency_key) == 1


def test_two_stale_sessions_preserve_the_first_disable(session_factory) -> None:
    with session_factory() as setup:
        add_memory_scope(setup, user_id="user-a", goal_id="goal-a")
        created = SQLAlchemyMemoryRepository(setup).create_or_get(
            preference_command(user_id="user-a", idempotency_key="stale-disable"),
            now=FIXED_NOW,
        )
        setup.commit()

    first_disabled_at = FIXED_NOW + timedelta(minutes=1)
    later_disabled_at = FIXED_NOW + timedelta(minutes=2)
    with session_factory() as session_a, session_factory() as session_b:
        cached_a = session_a.get(Memory, created.id)
        cached_b = session_b.get(Memory, created.id)
        assert cached_a is not None and cached_a.is_enabled is True
        assert cached_b is not None and cached_b.is_enabled is True

        first = SQLAlchemyMemoryRepository(session_a).disable(
            user_id="user-a",
            memory_id=created.id,
            reason="incorrect",
            now=first_disabled_at,
        )
        session_a.commit()

        stale_result = SQLAlchemyMemoryRepository(session_b).disable(
            user_id="user-a",
            memory_id=created.id,
            reason="privacy_request",
            now=later_disabled_at,
        )
        session_b.commit()

    assert stale_result.disabled_reason == first.disabled_reason == "incorrect"
    assert stale_result.disabled_at == first.disabled_at == first_disabled_at
    assert stale_result.updated_at == first.updated_at == first_disabled_at

    with session_factory() as reader:
        persisted = SQLAlchemyMemoryRepository(reader).get_by_id(
            user_id="user-a",
            memory_id=created.id,
            include_inactive=True,
            now=later_disabled_at,
        )

    assert persisted is not None
    assert persisted.disabled_reason == "incorrect"
    assert persisted.disabled_at == first_disabled_at
    assert persisted.updated_at == first_disabled_at


def test_unique_race_recovers_inside_savepoint_and_keeps_outer_transaction_usable(
    session_factory,
) -> None:
    with session_factory() as setup:
        add_memory_scope(setup, user_id="user-a", goal_id="goal-a")
        setup.commit()

    command = preference_command(user_id="user-a", idempotency_key="savepoint-race")
    with session_factory() as session_a:
        repository_a = SQLAlchemyMemoryRepository(session_a)
        competing_id: list[str] = []
        integrity_errors: list[str] = []
        outer_events: list[str] = []
        rolled_back_savepoints: list[str | None] = []
        bind = session_a.get_bind()
        connection_a = session_a.connection()
        outer_transaction = connection_a.get_transaction()
        raw_connection_a = connection_a.connection.driver_connection

        def insert_competing_row(connection, _name) -> None:
            if connection is connection_a:
                # The existing SQLite savepoint listener starts A's outer
                # transaction before this later listener runs. Session B can
                # therefore commit before SQLAlchemy emits A's real SAVEPOINT.
                assert raw_connection_a.in_transaction is True
                with session_factory() as session_b:
                    competing = SQLAlchemyMemoryRepository(session_b).create_or_get(
                        command,
                        now=FIXED_NOW,
                    )
                    session_b.commit()
                    competing_id.append(competing.id)
                assert raw_connection_a.in_transaction is True

        def capture_commit(connection) -> None:
            if connection is connection_a:
                outer_events.append("commit")

        def capture_rollback(connection) -> None:
            if connection is connection_a:
                outer_events.append("rollback")

        def capture_savepoint_rollback(connection, name, _context) -> None:
            if connection is connection_a:
                rolled_back_savepoints.append(name)

        def capture_integrity_error(exception_context) -> None:
            if "UNIQUE constraint failed: memories.user_id, memories.idempotency_key" in str(
                exception_context.original_exception
            ):
                integrity_errors.append(str(exception_context.original_exception))

        event.listen(bind, "savepoint", insert_competing_row, once=True)
        event.listen(bind, "handle_error", capture_integrity_error)
        event.listen(bind, "commit", capture_commit)
        event.listen(bind, "rollback", capture_rollback)
        event.listen(bind, "rollback_savepoint", capture_savepoint_rollback)
        try:
            recovered = repository_a.create_or_get(command, now=FIXED_NOW)

            assert competing_id
            assert recovered.id == competing_id[0]
            assert len(integrity_errors) == 1
            assert len(rolled_back_savepoints) == 1
            assert outer_events == []
            assert connection_a.get_transaction() is outer_transaction
            assert raw_connection_a.in_transaction is True

            session_a.add(
                User(
                    id="outer-transaction-user",
                    email="outer-transaction-user@example.com",
                    normalized_email="outer-transaction-user@example.com",
                    display_name="Outer Transaction User",
                )
            )
            session_a.flush()
            assert outer_events == []
            assert connection_a.get_transaction() is outer_transaction
            assert raw_connection_a.in_transaction is True

            session_a.commit()
            assert outer_events == ["commit"]
        finally:
            event.remove(bind, "savepoint", insert_competing_row)
            event.remove(bind, "handle_error", capture_integrity_error)
            event.remove(bind, "commit", capture_commit)
            event.remove(bind, "rollback", capture_rollback)
            event.remove(bind, "rollback_savepoint", capture_savepoint_rollback)

    with session_factory() as reader:
        assert reader.get(User, "outer-transaction-user") is not None
        assert _count_key(reader, user_id="user-a", key=command.idempotency_key) == 1


def test_unrelated_integrity_error_is_not_rewritten_as_idempotency_conflict(
    db_session: Session,
) -> None:
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-a")
    repository = SQLAlchemyMemoryRepository(db_session)

    def violate_check_constraint(flushing_session: Session, _context, _instances) -> None:
        memory = next(row for row in flushing_session.new if isinstance(row, Memory))
        memory.importance = 2.0

    event.listen(db_session, "before_flush", violate_check_constraint, once=True)
    try:
        with pytest.raises(IntegrityError) as error:
            repository.create_or_get(
                preference_command(user_id="user-a", idempotency_key="unrelated-integrity"),
                now=FIXED_NOW,
            )
    finally:
        event.remove(db_session, "before_flush", violate_check_constraint)

    assert "ck_memories_importance_range" in str(error.value.orig)
    assert _count_key(db_session, user_id="user-a", key="unrelated-integrity") == 0


def test_outer_rollback_removes_newly_flushed_memory(session_factory) -> None:
    with session_factory() as writer:
        add_memory_scope(writer, user_id="user-a", goal_id="goal-a")
        writer.commit()
        created = SQLAlchemyMemoryRepository(writer).create_or_get(
            preference_command(user_id="user-a", idempotency_key="outer-rollback"),
            now=FIXED_NOW,
        )
        writer.rollback()

    with session_factory() as reader:
        assert reader.get(Memory, created.id) is None


def test_outer_commit_exposes_newly_flushed_memory_to_fresh_session(session_factory) -> None:
    with session_factory() as writer:
        add_memory_scope(writer, user_id="user-a", goal_id="goal-a")
        writer.commit()
        created = SQLAlchemyMemoryRepository(writer).create_or_get(
            preference_command(user_id="user-a", idempotency_key="outer-commit"),
            now=FIXED_NOW,
        )
        writer.commit()

    with session_factory() as reader:
        assert reader.get(Memory, created.id) is not None


def test_repository_does_not_control_outer_session_lifecycle(
    db_session: Session,
    monkeypatch,
) -> None:
    add_memory_scope(db_session, user_id="user-a", goal_id="goal-a")

    def forbidden_lifecycle_call() -> None:
        pytest.fail("repository must not control the outer Session lifecycle")

    monkeypatch.setattr(db_session, "commit", forbidden_lifecycle_call)
    monkeypatch.setattr(db_session, "rollback", forbidden_lifecycle_call)
    monkeypatch.setattr(db_session, "close", forbidden_lifecycle_call)
    try:
        SQLAlchemyMemoryRepository(db_session).create_or_get(
            preference_command(user_id="user-a", idempotency_key="lifecycle-boundary"),
            now=FIXED_NOW,
        )
    finally:
        monkeypatch.undo()


def test_repository_source_constructs_neither_session_nor_engine() -> None:
    source = inspect.getsource(inspect.getmodule(SQLAlchemyMemoryRepository))
    tree = ast.parse(source)
    forbidden = {"Session", "sessionmaker", "create_engine", "Engine"}
    constructed = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
        and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr) in forbidden
    }

    assert constructed == set()
