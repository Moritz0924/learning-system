from __future__ import annotations

from uuid import uuid4

from tests.assessment.helpers import create_learning_goal


def test_submission_replays_same_request_and_rejects_changed_payload(client) -> None:
    goal = create_learning_goal(client, identity="assessment-v2-submit")
    created = client.post(
        "/api/assessments",
        headers=goal["headers"],
        json={
            "request_id": str(uuid4()),
            "goal_id": goal["goal_id"],
            "thread_id": "assessment-v2-submit-thread",
            "assessment_type": "daily",
            "knowledge_node_ids": [goal["knowledge_node_id"]],
        },
    ).json()
    request_id = str(uuid4())
    path = f"/api/assessments/{created['assessment_id']}/submit"
    choice_item = next(item for item in created["items"] if item["question_type"] == "choice")
    payload = {"request_id": request_id, "answers": {choice_item["item_id"]: "option-a"}}

    first = client.post(path, headers=goal["headers"], json=payload)
    replay = client.post(path, headers=goal["headers"], json=payload)
    conflict = client.post(path, headers=goal["headers"], json={**payload, "answers": {choice_item["item_id"]: "option-b"}})

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "graded", first.json()
    assert first.json()["attempt_id"] == replay.json()["attempt_id"]
    assert "reference_answer" not in str(first.json())
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "assessment.request_id_payload_conflict"
