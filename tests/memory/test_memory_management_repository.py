from __future__ import annotations

from datetime import timedelta

from backend.app.domain.memory import CreateMemoryCommand
from backend.app.infrastructure.persistence.repositories.memory_repository import SQLAlchemyMemoryRepository

from .helpers import FIXED_NOW, add_memory_scope, mastery_command, preference_command


def _goal_command(*, user_id: str, goal_id: str, key: str) -> CreateMemoryCommand:
    return CreateMemoryCommand(
        user_id=user_id,
        goal_id=goal_id,
        memory_type="long_term_goal",
        content={"title": "Ship M3", "target_outcome": "Close the memory write loop", "deadline": None},
        source_kind="explicit_user",
        idempotency_key=key,
    )


def test_repository_get_by_idempotency_key_is_always_user_owned(db_session) -> None:
    user_id, _ = add_memory_scope(db_session)
    other_user, _ = add_memory_scope(db_session, user_id="other-user", goal_id="other-goal")
    repository = SQLAlchemyMemoryRepository(db_session)
    created = repository.create_or_get(
        preference_command(user_id=user_id, idempotency_key="shared-key"),
        now=FIXED_NOW,
    )
    repository.create_or_get(
        preference_command(user_id=other_user, idempotency_key="shared-key"),
        now=FIXED_NOW,
    )

    assert repository.get_by_idempotency_key(user_id=user_id, idempotency_key="shared-key").id == created.id
    assert repository.get_by_idempotency_key(user_id="missing-user", idempotency_key="shared-key") is None


def test_management_list_combines_owned_filters_status_order_and_pagination(db_session) -> None:
    user_id, goal_id = add_memory_scope(db_session)
    repository = SQLAlchemyMemoryRepository(db_session)
    user_memory = repository.create_or_get(
        preference_command(user_id=user_id, idempotency_key="user-memory"),
        now=FIXED_NOW,
    )
    older_goal = repository.create_or_get(
        _goal_command(user_id=user_id, goal_id=goal_id, key="goal-old"),
        now=FIXED_NOW + timedelta(minutes=1),
    )
    active_goal = repository.create_or_get(
        mastery_command(
            user_id=user_id,
            goal_id=goal_id,
            source_kind="assessment",
            source_ref_id="attempt-1",
            idempotency_key="goal-active",
            expires_at=FIXED_NOW + timedelta(days=30),
        ),
        now=FIXED_NOW + timedelta(minutes=2),
    )
    repository.disable(user_id=user_id, memory_id=older_goal.id, reason="user_revoked", now=FIXED_NOW + timedelta(minutes=3))
    repository.create_or_get(
        mastery_command(
            user_id=user_id,
            goal_id=goal_id,
            source_kind="assessment",
            source_ref_id="attempt-expiring",
            idempotency_key="goal-expired",
            expires_at=FIXED_NOW + timedelta(minutes=4),
        ),
        now=FIXED_NOW + timedelta(minutes=3),
    )

    all_records = repository.list_memories(
        user_id=user_id,
        goal_id=goal_id,
        include_user_scope=True,
        status="all",
        limit=100,
        offset=0,
        now=FIXED_NOW + timedelta(minutes=5),
    )
    active_assessment = repository.list_memories(
        user_id=user_id,
        goal_id=goal_id,
        include_user_scope=False,
        memory_types={"mastery_summary"},
        source_kinds={"assessment"},
        status="active",
        limit=1,
        offset=0,
        now=FIXED_NOW + timedelta(minutes=5),
    )
    inactive = repository.list_memories(
        user_id=user_id,
        goal_id=goal_id,
        include_user_scope=False,
        status="inactive",
        limit=1,
        offset=1,
        now=FIXED_NOW + timedelta(minutes=5),
    )

    assert [record.id for record in all_records] == [
        all_records[0].id,  # expired row created newest
        active_goal.id,
        older_goal.id,
        user_memory.id,
    ]
    assert all_records[0].idempotency_key == "goal-expired"
    assert [record.id for record in active_assessment] == [active_goal.id]
    assert len(inactive) == 1
    assert inactive[0].id == older_goal.id


def test_management_list_rejects_invalid_limit_offset_status_and_source(db_session) -> None:
    repository = SQLAlchemyMemoryRepository(db_session)

    for kwargs in (
        {"limit": 0},
        {"offset": -1},
        {"status": "deleted"},
        {"source_kinds": {"provider_raw"}},
    ):
        try:
            repository.list_memories(user_id="user-1", **kwargs)
        except (ValueError, TypeError):
            pass
        else:
            raise AssertionError(f"expected invalid query to fail: {kwargs}")
