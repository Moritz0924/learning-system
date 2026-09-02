from __future__ import annotations

from sqlalchemy import select

from backend.app.models import LearningPlan, LearningStateSnapshot, SavedLearningNode
from tests.conftest import register_user


def _initialize(client, *, email: str) -> dict:
    identity = register_user(client, email=email)
    response = client.post(
        "/api/onboarding/initialize",
        headers=identity["headers"],
        json={
            "title": "Saved node goal",
            "target_outcome": "Persist active plan bookmarks",
            "deadline": "2026-12-31",
            "weekly_hours_target": 6,
            "learning_preferences": {"style": "coach_then_code"},
            "self_assessment": {"python_level": 3},
            "submitted_answers": {"questions": []},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    state = client.get(
        "/api/state/current",
        headers=identity["headers"],
        params={"goal_id": body["goal"]["goal_id"]},
    ).json()
    return {
        **identity,
        "goal_id": body["goal"]["goal_id"],
        "node_id": state["today_tasks"][0]["knowledge_node_id"],
    }


def test_saved_nodes_are_scoped_idempotent_and_persist(client) -> None:
    owner = _initialize(client, email="saved-owner@example.com")
    other = _initialize(client, email="saved-other@example.com")
    path = f"/api/saved-learning-nodes/{owner['node_id']}"

    for _ in range(2):
        response = client.put(
            path,
            headers=owner["headers"],
            json={"goal_id": owner["goal_id"]},
        )
        assert response.status_code == 204, response.text

    listed = client.get(
        "/api/saved-learning-nodes",
        headers=owner["headers"],
        params={"goal_id": owner["goal_id"]},
    )
    assert listed.status_code == 200
    assert listed.json() == {"knowledge_node_ids": [owner["node_id"]]}

    foreign_goal = client.get(
        "/api/saved-learning-nodes",
        headers=other["headers"],
        params={"goal_id": owner["goal_id"]},
    )
    foreign_node = client.put(
        path,
        headers=other["headers"],
        json={"goal_id": owner["goal_id"]},
    )
    assert foreign_goal.status_code == 404
    assert foreign_node.status_code == 404

    for _ in range(2):
        response = client.delete(
            path,
            headers=owner["headers"],
            params={"goal_id": owner["goal_id"]},
        )
        assert response.status_code == 204, response.text
    assert client.get(
        "/api/saved-learning-nodes",
        headers=owner["headers"],
        params={"goal_id": owner["goal_id"]},
    ).json() == {"knowledge_node_ids": []}


def test_saved_node_must_belong_to_the_current_active_plan(client) -> None:
    owner = _initialize(client, email="saved-active-plan@example.com")

    response = client.put(
        "/api/saved-learning-nodes/node-not-in-plan",
        headers=owner["headers"],
        json={"goal_id": owner["goal_id"]},
    )

    assert response.status_code == 404


def test_saved_nodes_from_a_replaced_plan_are_not_restored(client, db_session) -> None:
    owner = _initialize(client, email="saved-replaced-plan@example.com")
    assert client.put(
        f"/api/saved-learning-nodes/{owner['node_id']}",
        headers=owner["headers"],
        json={"goal_id": owner["goal_id"]},
    ).status_code == 204

    snapshot = db_session.scalar(
        select(LearningStateSnapshot).where(
            LearningStateSnapshot.user_id == owner["user_id"],
            LearningStateSnapshot.goal_id == owner["goal_id"],
        )
    )
    old_plan = db_session.get(LearningPlan, snapshot.active_plan_id)
    replacement = LearningPlan(
        id="plan-replacement-saved",
        user_id=owner["user_id"],
        goal_id=owner["goal_id"],
        version=old_plan.version + 1,
        status="active",
        valid_from=old_plan.valid_from,
        valid_to=old_plan.valid_to,
        plan_json=old_plan.plan_json,
        generated_by=old_plan.generated_by,
        rationale_json=old_plan.rationale_json,
    )
    old_plan.status = "superseded"
    db_session.add(replacement)
    snapshot.active_plan_id = replacement.id
    db_session.commit()

    listed = client.get(
        "/api/saved-learning-nodes",
        headers=owner["headers"],
        params={"goal_id": owner["goal_id"]},
    )
    assert listed.status_code == 200
    assert listed.json() == {"knowledge_node_ids": []}
    assert db_session.scalar(select(SavedLearningNode)) is not None
