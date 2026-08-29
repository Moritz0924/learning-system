from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.app.models import AgentRun, UserFeedback
from backend.app.application.feedback_service import build_eval_candidate
from tests.assessment.helpers import create_learning_goal
from tests.fakes.tutor import DeterministicTutorClient


@pytest.fixture(autouse=True)
def _test_tutor_model(monkeypatch):
    monkeypatch.setattr(
        "backend.app.application.config_service.RuntimeResolver.resolve_tutor_text",
        lambda _resolver, **_kwargs: DeterministicTutorClient(),
    )


def _run_id(client, session_factory, goal):
    response = client.post(
        "/api/tutor/chat",
        headers=goal["headers"],
        json={"goal_id": goal["goal_id"], "thread_id": "feedback-thread", "message": "Explain RAG."},
    )
    assert response.status_code == 200, response.text
    with session_factory() as session:
        return session.scalar(
            select(AgentRun.id)
            .where(AgentRun.user_id == goal["user_id"], AgentRun.goal_id == goal["goal_id"])
            .order_by(AgentRun.created_at.desc())
        )


def test_feedback_is_idempotent_and_conflicting_payload_is_rejected(client, session_factory) -> None:
    goal = create_learning_goal(client, identity="t3-feedback-idempotency")
    run_id = _run_id(client, session_factory, goal)
    body = {
        "helpful": True,
        "citation_correct": True,
        "difficulty_fit": False,
        "reason_code": "clear",
        "optional_comment": "Useful answer.",
    }
    path = f"/api/tutor/runs/{run_id}/feedback"
    first = client.post(path, headers=goal["headers"], json=body)
    replay = client.post(path, headers=goal["headers"], json=body)
    conflict = client.post(path, headers=goal["headers"], json={**body, "helpful": False})

    assert first.status_code == 201, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["feedback_id"] == first.json()["feedback_id"]
    assert replay.json()["replayed"] is True
    assert conflict.status_code == 409
    with session_factory() as session:
        assert session.query(UserFeedback).count() == 1


def test_feedback_requires_run_ownership(client, session_factory) -> None:
    owner = create_learning_goal(client, identity="t3-feedback-owner")
    other = create_learning_goal(client, identity="t3-feedback-other")
    run_id = _run_id(client, session_factory, owner)
    response = client.post(
        f"/api/tutor/runs/{run_id}/feedback",
        headers=other["headers"],
        json={"helpful": True, "reason_code": "clear"},
    )
    assert response.status_code == 404


def test_eval_candidate_requires_explicit_review_and_contains_no_raw_prompt() -> None:
    assert build_eval_candidate(
        feedback_id="feedback-1",
        run_id="run-1",
        helpful=False,
        reason_code="unsupported",
        sanitized_input="How does RAG work?",
        review_approved=False,
    ) is None
    candidate = build_eval_candidate(
        feedback_id="feedback-1",
        run_id="run-1",
        helpful=False,
        reason_code="unsupported",
        sanitized_input="How does RAG work?",
        review_approved=True,
    )
    assert candidate["dataset_version"] == "feedback-candidate-v1"
    assert "prompt" not in candidate
