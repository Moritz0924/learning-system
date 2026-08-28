from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from tests.conftest import register_user


def create_learning_goal(client: TestClient, *, identity: str) -> dict:
    user = register_user(
        client,
        email=f"{identity}@example.com",
        display_name="Assessment Contract Learner",
    )
    response = client.post(
        "/api/onboarding/initialize",
        headers=user["headers"],
        json={
            "title": "Learn secure assessment design",
            "target_outcome": "Build an assessment API without answer leakage",
            "deadline": "2026-09-01",
            "weekly_hours_target": 8,
            "learning_preferences": {"style": "examples_first"},
            "self_assessment": {"python_level": 3},
            "submitted_answers": {
                "questions": [
                    {"node_code": "python_foundations", "is_correct": True}
                ]
            },
        },
    )
    assert response.status_code == 201, response.text
    goal = response.json()["goal"]
    state = client.get(
        "/api/state/current",
        headers=user["headers"],
        params={"goal_id": goal["goal_id"]},
    )
    assert state.status_code == 200, state.text
    return {
        **user,
        "goal_id": goal["goal_id"],
        "knowledge_node_id": state.json()["today_tasks"][0]["knowledge_node_id"],
    }


def create_assessment(
    client: TestClient,
    goal: dict,
    *,
    assessment_type: str = "daily",
    thread_id: str = "assessment-contract-thread",
    locale: str = "en-US",
):
    return client.post(
        "/api/assessments",
        headers=goal["headers"],
        json={
            "request_id": str(uuid4()),
            "goal_id": goal["goal_id"],
            "thread_id": thread_id,
            "assessment_type": assessment_type,
            "knowledge_node_ids": [goal["knowledge_node_id"]],
            "locale": locale,
        },
    )
