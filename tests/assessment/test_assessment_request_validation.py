from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import AgentRun, Assessment
from tests.assessment.helpers import create_assessment, create_learning_goal
from tests.conftest import register_user


def test_submit_requires_request_id(client) -> None:
    goal = create_learning_goal(client, identity="submit-request-id")
    assessment = create_assessment(client, goal).json()

    response = client.post(
        f"/api/assessments/{assessment['assessment_id']}/submit",
        headers=goal["headers"],
        json={"answers": {}},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "request_id"]


def test_submit_request_rejects_malformed_uuid_and_unknown_fields(client) -> None:
    goal = create_learning_goal(client, identity="malformed-submit-request")
    assessment = create_assessment(client, goal).json()
    path = f"/api/assessments/{assessment['assessment_id']}/submit"

    malformed = client.post(
        path,
        headers=goal["headers"],
        json={"request_id": "not-a-uuid", "answers": {}},
    )
    extra = client.post(
        path,
        headers=goal["headers"],
        json={"request_id": str(uuid4()), "answers": {}, "reference_answer": "leak"},
    )

    assert malformed.status_code == 422
    assert malformed.json()["detail"][0]["loc"] == ["body", "request_id"]
    assert extra.status_code == 422
    assert extra.json()["detail"][0]["type"] == "extra_forbidden"


def test_submit_request_rejects_non_string_and_oversized_answers(client) -> None:
    goal = create_learning_goal(client, identity="bounded-submit-request")
    assessment = create_assessment(client, goal).json()
    path = f"/api/assessments/{assessment['assessment_id']}/submit"
    item_id = assessment["items"][0]["item_id"]

    non_string = client.post(
        path,
        headers=goal["headers"],
        json={"request_id": str(uuid4()), "answers": {item_id: 42}},
    )
    oversized = client.post(
        path,
        headers=goal["headers"],
        json={"request_id": str(uuid4()), "answers": {item_id: "x" * 8193}},
    )

    assert non_string.status_code == 422
    assert oversized.status_code == 422


def test_submit_request_limits_answer_count(client) -> None:
    goal = create_learning_goal(client, identity="answer-count-limit")
    assessment = create_assessment(client, goal).json()

    response = client.post(
        f"/api/assessments/{assessment['assessment_id']}/submit",
        headers=goal["headers"],
        json={
            "request_id": str(uuid4()),
            "answers": {f"item-{index}": "answer" for index in range(101)},
        },
    )

    assert response.status_code == 422


def test_create_request_rejects_unknown_assessment_type_at_http_boundary(client) -> None:
    goal = create_learning_goal(client, identity="assessment-type-boundary")

    with TestClient(client.app, raise_server_exceptions=False) as safe_client:
        response = safe_client.post(
            "/api/assessments",
            headers=goal["headers"],
            json={
                "request_id": str(uuid4()),
                "goal_id": goal["goal_id"],
                "thread_id": "assessment-type-thread",
                "assessment_type": "surprise",
                "knowledge_node_ids": [goal["knowledge_node_id"]],
            },
        )

    assert response.status_code == 422


def test_unknown_item_id_returns_stable_422_without_claiming_assessment(
    client,
    session_factory,
) -> None:
    goal = create_learning_goal(client, identity="unknown-assessment-item")
    assessment = create_assessment(client, goal).json()
    path = f"/api/assessments/{assessment['assessment_id']}/submit"

    unknown = client.post(
        path,
        headers=goal["headers"],
        json={
            "request_id": str(uuid4()),
            "answers": {"item-from-another-assessment": "answer"},
        },
    )

    assert unknown.status_code == 422
    assert unknown.json()["detail"] == {
        "code": "assessment.unknown_item_id",
        "message": "Submitted answers contain unknown assessment item IDs.",
    }
    with session_factory() as session:
        assert session.get(Assessment, assessment["assessment_id"]).status == "active"

    valid = client.post(
        path,
        headers=goal["headers"],
        json={
            "request_id": str(uuid4()),
            "answers": {assessment["items"][0]["item_id"]: "valid answer"},
        },
    )
    assert valid.status_code == 200, valid.text


def test_partial_and_blank_answers_keep_existing_scoring_behavior(client) -> None:
    goal = create_learning_goal(client, identity="partial-blank-answers")
    assessment = create_assessment(client, goal).json()

    response = client.post(
        f"/api/assessments/{assessment['assessment_id']}/submit",
        headers=goal["headers"],
        json={
            "request_id": str(uuid4()),
            "answers": {assessment["items"][0]["item_id"]: ""},
        },
    )

    assert response.status_code == 200, response.text
    results = {item["item_id"]: item for item in response.json()["answers"]}
    assert set(results) == {item["item_id"] for item in assessment["items"]}
    assert all(item["score"] == 0 for item in results.values())


def test_cross_user_submit_is_404_and_duplicate_submit_is_409(client) -> None:
    goal = create_learning_goal(client, identity="assessment-submit-owner")
    other = register_user(client, email="assessment-submit-other@example.com")
    assessment = create_assessment(client, goal).json()
    path = f"/api/assessments/{assessment['assessment_id']}/submit"
    body = {
        "request_id": str(uuid4()),
        "answers": {assessment["items"][0]["item_id"]: "answer"},
    }

    cross_user = client.post(path, headers=other["headers"], json=body)
    first = client.post(path, headers=goal["headers"], json=body)
    duplicate = client.post(
        path,
        headers=goal["headers"],
        json={**body, "request_id": str(uuid4())},
    )

    assert cross_user.status_code == 404
    assert first.status_code == 200, first.text
    assert duplicate.status_code == 409


def test_create_assessment_audits_the_requested_thread_id(client, session_factory) -> None:
    goal = create_learning_goal(client, identity="assessment-thread-audit")

    response = create_assessment(
        client,
        goal,
        thread_id="caller-provided-assessment-thread",
    )

    assert response.status_code == 201, response.text
    with session_factory() as session:
        run = session.scalar(
            select(AgentRun)
            .where(
                AgentRun.user_id == goal["user_id"],
                AgentRun.trigger_type == "assessment_due",
            )
            .order_by(AgentRun.created_at.desc())
        )
    assert run.thread_id == "caller-provided-assessment-thread"


def test_concurrent_duplicate_submissions_only_grade_once(client) -> None:
    goal = create_learning_goal(client, identity="concurrent-assessment-submit")
    assessment = create_assessment(client, goal).json()
    path = f"/api/assessments/{assessment['assessment_id']}/submit"
    answers = {assessment["items"][0]["item_id"]: "answer"}

    def submit_once() -> int:
        response = client.post(
            path,
            headers=goal["headers"],
            json={"request_id": str(uuid4()), "answers": answers},
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _: submit_once(), range(2)))

    assert statuses == [200, 409]
