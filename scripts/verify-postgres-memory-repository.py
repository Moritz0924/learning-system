from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from threading import Event
from uuid import uuid4

from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker

from backend.app.application.memory_privacy_service import MemoryPrivacyService
from backend.app.domain.memory import CreateMemoryCommand, MemoryPrivacySettings
from backend.app.infrastructure.persistence.repositories.memory_repository import SQLAlchemyMemoryRepository
from backend.app.models import LearnerProfile, LearningGoal, Memory, User


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        raise AssertionError("PostgreSQL memory repository verification requires PostgreSQL")
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    suffix = uuid4().hex
    user_a = f"memory-repository-a-{suffix}"
    user_b = f"memory-repository-b-{suffix}"
    goal_a = f"memory-repository-goal-a-{suffix}"
    goal_b = f"memory-repository-goal-b-{suffix}"
    try:
        _create_scopes(factory, user_a=user_a, user_b=user_b, goal_a=goal_a, goal_b=goal_b)
        _verify_concurrent_idempotency(factory, user_id=user_a, suffix=suffix)
        _verify_user_row_lock(factory, user_id=user_a)
        _verify_cross_user_isolation(factory, user_a=user_a, user_b=user_b, suffix=suffix)
        _verify_outer_rollback(factory, user_id=user_a, suffix=suffix)
    finally:
        _cleanup(factory, user_ids=[user_a, user_b])
        engine.dispose()


def _create_scopes(factory, *, user_a: str, user_b: str, goal_a: str, goal_b: str) -> None:
    with factory() as session:
        for user_id in (user_a, user_b):
            email = f"{user_id}@example.invalid"
            session.add(
                User(
                    id=user_id,
                    email=email,
                    normalized_email=email,
                    display_name="PostgreSQL memory verification",
                )
            )
        session.flush()
        for user_id in (user_a, user_b):
            session.add(
                LearnerProfile(
                    user_id=user_id,
                    privacy_settings={"data_scope": "postgres-verification"},
                )
            )
        session.add_all(
            [
                LearningGoal(
                    id=goal_a,
                    user_id=user_a,
                    title="Memory verification A",
                    target_outcome="Verify repository boundaries",
                    weekly_hours_target=1,
                ),
                LearningGoal(
                    id=goal_b,
                    user_id=user_b,
                    title="Memory verification B",
                    target_outcome="Verify repository boundaries",
                    weekly_hours_target=1,
                ),
            ]
        )
        session.commit()


def _preference_command(*, user_id: str, key: str, value: str = "examples") -> CreateMemoryCommand:
    return CreateMemoryCommand(
        user_id=user_id,
        memory_type="learning_preference",
        content={"preference_key": "explanation_style", "preference_value": value},
        source_kind="explicit_user",
        idempotency_key=key,
    )


def _verify_concurrent_idempotency(factory, *, user_id: str, suffix: str) -> None:
    command = _preference_command(
        user_id=user_id,
        key=f"memory-v1:explicit:concurrent-{suffix}",
    )
    start = Event()

    def create() -> str:
        with factory() as session:
            start.wait(timeout=5)
            record = SQLAlchemyMemoryRepository(session).create_or_get(command)
            session.commit()
            return record.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create) for _ in range(2)]
        start.set()
        memory_ids = [future.result(timeout=15) for future in futures]
    if len(set(memory_ids)) != 1:
        raise AssertionError(f"concurrent idempotency produced multiple records: {memory_ids!r}")


def _verify_user_row_lock(factory, *, user_id: str) -> None:
    contender_started = Event()
    contender_finished = Event()
    with factory() as locking_session:
        MemoryPrivacyService(locking_session).get(user_id=user_id, for_update=True)

        def update_privacy() -> None:
            with factory() as contender:
                contender_started.set()
                MemoryPrivacyService(contender).update(
                    user_id=user_id,
                    settings=MemoryPrivacySettings(allow_system_inference=True),
                )
                contender.commit()
                contender_finished.set()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(update_privacy)
            if not contender_started.wait(timeout=5):
                raise AssertionError("privacy lock contender did not start")
            if contender_finished.wait(timeout=0.25):
                raise AssertionError("privacy update bypassed the learner profile row lock")
            locking_session.commit()
            future.result(timeout=10)
    if not contender_finished.is_set():
        raise AssertionError("privacy update did not complete after releasing the row lock")


def _verify_cross_user_isolation(factory, *, user_a: str, user_b: str, suffix: str) -> None:
    shared_key = f"memory-v1:explicit:shared-{suffix}"
    with factory() as session:
        repository = SQLAlchemyMemoryRepository(session)
        record_a = repository.create_or_get(_preference_command(user_id=user_a, key=shared_key))
        record_b = repository.create_or_get(_preference_command(user_id=user_b, key=shared_key))
        if record_a.id == record_b.id:
            raise AssertionError("different users unexpectedly shared a memory record")
        if repository.get_by_id(user_id=user_b, memory_id=record_a.id, include_inactive=True) is not None:
            raise AssertionError("cross-user memory lookup unexpectedly succeeded")
        session.commit()


def _verify_outer_rollback(factory, *, user_id: str, suffix: str) -> None:
    key = f"memory-v1:explicit:outer-rollback-{suffix}"
    with factory() as session:
        record = SQLAlchemyMemoryRepository(session).create_or_get(
            _preference_command(user_id=user_id, key=key, value="rollback")
        )
        memory_id = record.id
        session.rollback()  # The repository flush must remain controlled by the outer rollback.
    with factory() as reader:
        if SQLAlchemyMemoryRepository(reader).get_by_id(
            user_id=user_id,
            memory_id=memory_id,
            include_inactive=True,
        ) is not None:
            raise AssertionError("outer rollback did not remove the uncommitted memory")


def _cleanup(factory, *, user_ids: list[str]) -> None:
    with factory() as session:
        session.execute(delete(Memory).where(Memory.user_id.in_(user_ids)))
        session.execute(delete(LearningGoal).where(LearningGoal.user_id.in_(user_ids)))
        session.execute(delete(LearnerProfile).where(LearnerProfile.user_id.in_(user_ids)))
        session.execute(delete(User).where(User.id.in_(user_ids)))
        session.commit()


if __name__ == "__main__":
    main()
