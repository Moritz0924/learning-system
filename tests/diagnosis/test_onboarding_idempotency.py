from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from uuid import uuid4

from sqlalchemy import func, select

from backend.app.api.schemas.onboarding import OnboardingInitializeRequest
from backend.app.application.onboarding_service import OnboardingService
from backend.app.models import BaselineDiagnostic, LearningGoal, LearningPlan, LearningStateSnapshot
from tests.conftest import register_user
from tests.diagnosis.helpers import initialize_payload


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_same_user_request_id_replays_first_success_without_duplicate_writes(
    client, session_factory
) -> None:
    identity = register_user(client, email="idempotent@example.com")
    payload = initialize_payload()

    first = client.post(
        "/api/onboarding/initialize", headers=identity["headers"], json=payload
    )
    replay = client.post(
        "/api/onboarding/initialize", headers=identity["headers"], json=payload
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is True
    assert replay.json()["goal"] == first.json()["goal"]
    assert replay.json()["diagnosis"] == first.json()["diagnosis"]
    with session_factory() as session:
        assert _count(session, LearningGoal) == 1
        assert _count(session, BaselineDiagnostic) == 1
        assert _count(session, LearningPlan) == 1
        assert _count(session, LearningStateSnapshot) == 1

def test_same_request_id_is_isolated_between_users(client, session_factory) -> None:
    first_user = register_user(client, email="idempotent-a@example.com")
    second_user = register_user(client, email="idempotent-b@example.com")
    payload = initialize_payload()

    first = client.post(
        "/api/onboarding/initialize", headers=first_user["headers"], json=payload
    )
    second = client.post(
        "/api/onboarding/initialize", headers=second_user["headers"], json=payload
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["goal"]["goal_id"] != second.json()["goal"]["goal_id"]
    assert second.json()["replayed"] is False
    with session_factory() as session:
        assert _count(session, LearningGoal) == 2
        assert _count(session, BaselineDiagnostic) == 2


def test_concurrent_same_request_id_returns_one_workspace(
    session_factory, monkeypatch
) -> None:
    from backend.app.models import User
    from backend.app.services.curriculum import ensure_curriculum_seeded

    user_id = f"user-{uuid4()}"
    with session_factory() as session:
        session.add(
            User(
                id=user_id,
                email="concurrent-onboarding@example.com",
                normalized_email="concurrent-onboarding@example.com",
                display_name="Concurrent",
                status="active",
            )
        )
        ensure_curriculum_seeded(session)
        session.commit()

    request = OnboardingInitializeRequest.model_validate(initialize_payload())
    barrier = Barrier(2)
    lock = Lock()
    initial_checks = 0
    original = OnboardingService._find_existing_diagnostic

    def synchronized_find(self, *, user_id: str, request_id: str):
        nonlocal initial_checks
        existing = original(self, user_id=user_id, request_id=request_id)
        if existing is None:
            with lock:
                initial_checks += 1
                should_wait = initial_checks <= 2
            if should_wait:
                barrier.wait(timeout=5)
        return existing

    monkeypatch.setattr(
        OnboardingService, "_find_existing_diagnostic", synchronized_find
    )

    def initialize_once():
        with session_factory() as session:
            return OnboardingService(session).initialize(user_id=user_id, request=request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: initialize_once(), range(2)))

    assert sorted(result.replayed for result in results) == [False, True]
    assert len({result.goal.id for result in results}) == 1
    with session_factory() as session:
        assert _count(session, LearningGoal) == 1
        assert _count(session, BaselineDiagnostic) == 1
        assert _count(session, LearningPlan) == 1
        assert _count(session, LearningStateSnapshot) == 1
