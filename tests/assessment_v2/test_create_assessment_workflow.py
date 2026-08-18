from __future__ import annotations

from uuid import uuid4

from tests.assessment.helpers import create_learning_goal


def test_create_assessment_is_idempotent_and_publicly_redacted(client) -> None:
    goal = create_learning_goal(client, identity="assessment-v2-create")
    request_id = str(uuid4())
    payload = {
        "request_id": request_id,
        "goal_id": goal["goal_id"],
        "thread_id": "assessment-v2-thread",
        "assessment_type": "daily",
        "knowledge_node_ids": [goal["knowledge_node_id"]],
    }

    first = client.post("/api/assessments", headers=goal["headers"], json=payload)
    replay = client.post("/api/assessments", headers=goal["headers"], json=payload)
    conflict = client.post(
        "/api/assessments",
        headers=goal["headers"],
        json={**payload, "assessment_type": "weekly"},
    )

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json()["assessment_id"] == first.json()["assessment_id"]
    assert first.json()["items"]
    assert "reference_answer" not in str(first.json())
    assert "rubric" not in str(first.json())
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "assessment.request_id_payload_conflict"
