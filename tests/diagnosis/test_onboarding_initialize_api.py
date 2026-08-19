from __future__ import annotations

from sqlalchemy import func, select

from backend.app.models import (
    BaselineDiagnostic,
    LearnerProfile,
    LearningGoal,
    LearningPlan,
    LearningStateSnapshot,
    MasteryRecord,
    PlanTask,
)
from backend.app.domain.diagnosis.validation import DiagnosisValidationError
from tests.conftest import register_user
from tests.diagnosis.helpers import initialize_payload


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_atomic_initialize_persists_real_scoring_workspace_once(client, session_factory) -> None:
    identity = register_user(client, email="real-diagnosis@example.com")
    payload = initialize_payload(all_correct=False)

    response = client.post(
        "/api/onboarding/initialize", headers=identity["headers"], json=payload
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["replayed"] is False
    assert body["goal"]["user_id"] == identity["user_id"]
    assert body["diagnosis"]["template_version"] == "ai_app_dev_v1"
    assert len(body["diagnosis"]["template_hash"]) == 64
    assert body["diagnosis"]["score_breakdown"]["all_baseline_nodes_passed"] is False
    assert body["diagnosis"]["entry_node_code"] == "python_foundations"
    assert body["state"]["active_plan"]["id"] == body["diagnosis"]["active_plan_id"]

    with session_factory() as session:
        assert _count(session, LearningGoal) == 1
        assert _count(session, LearnerProfile) == 1
        assert _count(session, BaselineDiagnostic) == 1
        assert _count(session, LearningPlan) == 1
        assert _count(session, PlanTask) == 1
        assert _count(session, LearningStateSnapshot) == 1
        assert _count(session, MasteryRecord) == 5
        diagnostic = session.scalar(select(BaselineDiagnostic))
        assert diagnostic.request_id == payload["request_id"]
        assert diagnostic.template_version == "ai_app_dev_v1"
        assert diagnostic.score_breakdown["nodes"]["python_foundations"]["objective_score"] == 0


def test_initialize_publishes_task_in_requested_locale(client, session_factory) -> None:
    identity = register_user(client, email="localized-task@example.com")
    payload = initialize_payload(all_correct=False)
    payload["locale"] = "zh-CN"

    response = client.post(
        "/api/onboarding/initialize", headers=identity["headers"], json=payload
    )

    assert response.status_code == 201, response.text
    with session_factory() as session:
        task = session.scalar(select(PlanTask))
        assert task is not None
        assert task.title == "学习 python foundations"
        assert task.objective == "掌握 python foundations 的基础知识。"


def test_initialize_request_is_strict_and_rejects_client_identity(client) -> None:
    identity = register_user(client, email="strict-initialize@example.com")
    payload = initialize_payload()
    payload["user_id"] = identity["user_id"]

    response = client.post(
        "/api/onboarding/initialize", headers=identity["headers"], json=payload
    )

    assert response.status_code == 422


def test_initialize_rejects_template_version_and_invalid_answers_without_writes(
    client, session_factory
) -> None:
    identity = register_user(client, email="invalid-diagnosis@example.com")
    wrong_version = initialize_payload(template_version="ai_app_dev_v999")
    invalid_option = initialize_payload()
    invalid_option["knowledge_answers"][0]["selected_option_id"] = "forged-option"

    version_response = client.post(
        "/api/onboarding/initialize", headers=identity["headers"], json=wrong_version
    )
    option_response = client.post(
        "/api/onboarding/initialize", headers=identity["headers"], json=invalid_option
    )

    assert version_response.status_code == 422
    assert version_response.json()["detail"]["code"] == "diagnosis.template_version_mismatch"
    assert option_response.status_code == 422
    assert option_response.json()["detail"]["code"] == "diagnosis.invalid_diagnostic_option"
    with session_factory() as session:
        assert _count(session, LearningGoal) == 0
        assert _count(session, BaselineDiagnostic) == 0
        assert _count(session, LearningPlan) == 0
        assert _count(session, LearningStateSnapshot) == 0


def test_template_loading_failure_returns_503_without_writes(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="template-failure@example.com")

    def fail_template_load(**kwargs):
        raise DiagnosisValidationError("invalid_template", "injected template failure")

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.DEFAULT_DIAGNOSTIC_TEMPLATE_REPOSITORY.load",
        fail_template_load,
    )

    response = client.post(
        "/api/onboarding/initialize",
        headers=identity["headers"],
        json=initialize_payload(),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "diagnosis.template_invalid"
    with session_factory() as session:
        assert _count(session, LearningGoal) == 0
        assert _count(session, BaselineDiagnostic) == 0


def test_scoring_failure_rolls_back_goal(client, session_factory, monkeypatch) -> None:
    identity = register_user(client, email="score-rollback@example.com")

    def fail_scoring(**kwargs):
        raise RuntimeError("injected scoring failure")

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.score_diagnosis", fail_scoring
    )
    try:
        client.post(
            "/api/onboarding/initialize",
            headers=identity["headers"],
            json=initialize_payload(),
        )
    except RuntimeError as exc:
        assert str(exc) == "injected scoring failure"
    else:
        raise AssertionError("the injected scoring failure must reach the test client")

    with session_factory() as session:
        assert _count(session, LearningGoal) == 0
        assert _count(session, BaselineDiagnostic) == 0


def test_plan_failure_rolls_back_diagnostic_and_goal(client, session_factory, monkeypatch) -> None:
    identity = register_user(client, email="plan-rollback@example.com")

    def fail_plan(*args, **kwargs):
        raise RuntimeError("injected plan failure")

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.OnboardingService._create_initial_plan",
        fail_plan,
    )
    try:
        client.post(
            "/api/onboarding/initialize",
            headers=identity["headers"],
            json=initialize_payload(),
        )
    except RuntimeError as exc:
        assert str(exc) == "injected plan failure"
    else:
        raise AssertionError("the injected plan failure must reach the test client")

    with session_factory() as session:
        assert _count(session, LearningGoal) == 0
        assert _count(session, BaselineDiagnostic) == 0
        assert _count(session, LearningPlan) == 0


def test_snapshot_failure_rolls_back_entire_workspace(client, session_factory, monkeypatch) -> None:
    identity = register_user(client, email="snapshot-rollback@example.com")

    def fail_snapshot(*args, **kwargs):
        raise RuntimeError("injected snapshot failure")

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.OnboardingService._create_state_snapshot",
        fail_snapshot,
    )
    try:
        client.post(
            "/api/onboarding/initialize",
            headers=identity["headers"],
            json=initialize_payload(),
        )
    except RuntimeError as exc:
        assert str(exc) == "injected snapshot failure"
    else:
        raise AssertionError("the injected snapshot failure must reach the test client")

    with session_factory() as session:
        assert _count(session, LearningGoal) == 0
        assert _count(session, BaselineDiagnostic) == 0
        assert _count(session, LearningPlan) == 0
        assert _count(session, PlanTask) == 0
        assert _count(session, MasteryRecord) == 0
        assert _count(session, LearningStateSnapshot) == 0
