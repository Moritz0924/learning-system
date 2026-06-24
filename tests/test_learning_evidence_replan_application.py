from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import text


def _create_goal_and_diagnosis(client, user_id: str = "evidence-user") -> dict:
    goal_response = client.post(
        "/api/goals",
        json={
            "user_id": user_id,
            "email": f"{user_id}@example.com",
            "display_name": "Evidence Learner",
            "title": "Learn AI application development",
            "target_outcome": "Build a working RAG tutor",
            "deadline": "2026-08-15",
            "weekly_hours_target": 10,
            "learning_preferences": {"style": "coach_then_code"},
        },
    )
    assert goal_response.status_code == 201
    goal = goal_response.json()

    diagnosis_response = client.post(
        "/api/onboarding/diagnosis",
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "self_assessment": {
                "python_level": 4,
                "api_level": 3,
                "llm_level": 2,
                "rag_level": 1,
                "langgraph_level": 0,
            },
            "submitted_answers": {
                "questions": [
                    {"node_code": "python_foundations", "is_correct": True},
                    {"node_code": "fastapi_basics", "is_correct": True},
                    {"node_code": "llm_api_basics", "is_correct": False},
                    {"node_code": "rag_foundations", "is_correct": False},
                ]
            },
        },
    )
    assert diagnosis_response.status_code == 201
    return goal


def _state(client, goal: dict) -> dict:
    response = client.get(
        f"/api/state/current?goal_id={goal['goal_id']}",
        headers={"X-User-Id": goal["user_id"]},
    )
    assert response.status_code == 200
    return response.json()


def _create_low_score_assessment(client, goal: dict, knowledge_node_id: str) -> None:
    assessment_response = client.post(
        "/api/assessments",
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "evidence-thread",
            "assessment_type": "daily",
            "knowledge_node_ids": [knowledge_node_id],
        },
    )
    assert assessment_response.status_code == 201
    assessment = assessment_response.json()
    submit_response = client.post(
        f"/api/assessments/{assessment['assessment_id']}/submit",
        json={
            "user_id": goal["user_id"],
            "answers": {item["item_id"]: "wrong" for item in assessment["items"]},
        },
    )
    assert submit_response.status_code == 200
    assert submit_response.json()["score"] < 70


def test_task_start_and_complete_records_sessions_events_and_refreshes_state(client, session_factory):
    goal = _create_goal_and_diagnosis(client)
    initial_state = _state(client, goal)
    task = initial_state["today_tasks"][0]

    start_response = client.post(
        f"/api/tasks/{task['id']}/start",
        json={"user_id": goal["user_id"]},
    )
    assert start_response.status_code == 200
    started = start_response.json()
    assert started["task"]["status"] == "active"
    assert started["session"]["status"] == "active"
    assert started["session"]["task_id"] == task["id"]

    complete_response = client.post(
        f"/api/tasks/{task['id']}/complete",
        json={
            "user_id": goal["user_id"],
            "duration_minutes": 25,
            "evidence": {"note": "Finished the first learning task."},
        },
    )
    assert complete_response.status_code == 200
    completed = complete_response.json()
    assert completed["task"]["status"] == "completed"
    assert completed["session"]["status"] == "completed"
    assert completed["observer_decision"]

    with session_factory() as session:
        assert session.execute(text("select count(*) from learning_sessions")).scalar_one() == 1
        assert session.execute(text("select count(*) from learning_events")).scalar_one() >= 2
        task_status = session.execute(
            text("select status from plan_tasks where id = :task_id"),
            {"task_id": task["id"]},
        ).scalar_one()
        assert task_status == "completed"
        event_types = session.execute(
            text("select event_type from learning_events order by occurred_at")
        ).scalars().all()
        assert "task_started" in event_types
        assert "task_completed" in event_types

    refreshed = _state(client, goal)
    assert refreshed["current_state"]["completion_rate_7d"] == 1.0
    assert refreshed["current_state"]["recent_learning_events"][-1]["event_type"] == "task_completed"
    assert refreshed["today_tasks"][0]["status"] == "completed"


def test_replan_preview_then_apply_creates_new_plan_tasks_and_audit_event(client, session_factory):
    goal = _create_goal_and_diagnosis(client, user_id="apply-user")
    before = _state(client, goal)
    old_plan_id = before["active_plan"]["id"]
    old_version = before["active_plan"]["version"]
    node_id = before["today_tasks"][0]["knowledge_node_id"]
    _create_low_score_assessment(client, goal, node_id)

    replan_response = client.post(
        "/api/plans/replan",
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "apply-thread",
            "message": "Please add focused review before continuing.",
        },
    )
    assert replan_response.status_code == 200
    proposed = replan_response.json()
    assert proposed["status"] == "proposed"
    assert proposed["decision"] == "remediate"
    assert proposed["new_plan_id"] is None

    apply_response = client.post(
        f"/api/plans/adjustments/{proposed['adjustment_id']}/apply",
        json={"user_id": goal["user_id"], "goal_id": goal["goal_id"]},
    )
    assert apply_response.status_code == 200
    applied = apply_response.json()
    assert applied["status"] == "applied"
    assert applied["new_plan_id"]
    assert applied["new_plan_id"] != old_plan_id
    assert applied["active_plan"]["version"] == old_version + 1
    assert applied["created_tasks"]
    assert applied["created_tasks"][0]["task_type"] == "review"

    refreshed = _state(client, goal)
    assert refreshed["active_plan"]["id"] == applied["new_plan_id"]
    assert refreshed["active_plan"]["version"] == old_version + 1
    assert refreshed["latest_plan_adjustment"]["adjustment_id"] == proposed["adjustment_id"]
    assert refreshed["latest_plan_adjustment"]["status"] == "applied"
    assert refreshed["today_tasks"][0]["task_type"] == "review"

    with session_factory() as session:
        old_status = session.execute(
            text("select status from learning_plans where id = :plan_id"),
            {"plan_id": old_plan_id},
        ).scalar_one()
        assert old_status == "replaced"
        adjustment_row = session.execute(
            text("select status, new_plan_id from plan_adjustments where id = :adjustment_id"),
            {"adjustment_id": proposed["adjustment_id"]},
        ).one()
        assert adjustment_row.status == "applied"
        assert adjustment_row.new_plan_id == applied["new_plan_id"]
        event_count = session.execute(
            text("select count(*) from learning_events where event_type = 'plan_adjustment_applied'")
        ).scalar_one()
        assert event_count == 1


def test_keep_adjustment_cannot_be_applied(client):
    goal = _create_goal_and_diagnosis(client, user_id="keep-user")

    replan_response = client.post(
        "/api/plans/replan",
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "keep-thread",
            "message": "Please check whether anything needs to change.",
        },
    )
    assert replan_response.status_code == 200
    proposed = replan_response.json()
    assert proposed["decision"] == "keep"

    apply_response = client.post(
        f"/api/plans/adjustments/{proposed['adjustment_id']}/apply",
        json={"user_id": goal["user_id"], "goal_id": goal["goal_id"]},
    )
    assert apply_response.status_code == 409
    assert "no applicable plan patch" in apply_response.json()["detail"]


def test_reduce_and_advance_patch_application_rules(client, session_factory):
    goal = _create_goal_and_diagnosis(client, user_id="patch-rules-user")
    state = _state(client, goal)
    active_plan_id = state["active_plan"]["id"]

    with session_factory() as session:
        reduce_id = "adjustment-test-reduce"
        session.execute(
            text(
                """
                insert into plan_adjustments (
                    id, user_id, goal_id, previous_plan_id, new_plan_id, trigger_type,
                    decision, evidence_json, before_snapshot, after_snapshot, plan_patch,
                    change_summary, rationale_json, status, created_at
                ) values (
                    :id, :user_id, :goal_id, :previous_plan_id, null, 'manual',
                    'reduce', '{}', '{}', '{}', :plan_patch,
                    '{}', '{}', 'proposed', :created_at
                )
                """
            ),
            {
                "id": reduce_id,
                "user_id": goal["user_id"],
                "goal_id": goal["goal_id"],
                "previous_plan_id": active_plan_id,
                "plan_patch": json.dumps({"load_multiplier": 0.8, "defer_nonessential": True}),
                "created_at": datetime.utcnow().isoformat(),
            },
        )
        session.commit()

    reduce_response = client.post(
        "/api/plans/adjustments/adjustment-test-reduce/apply",
        json={"user_id": goal["user_id"], "goal_id": goal["goal_id"]},
    )
    assert reduce_response.status_code == 200
    reduced = reduce_response.json()
    assert reduced["created_tasks"][0]["estimated_minutes"] == 36

    with session_factory() as session:
        advance_id = "adjustment-test-advance"
        current_plan_id = session.execute(
            text(
                "select active_plan_id from learning_state_snapshots where user_id = :user_id and goal_id = :goal_id"
            ),
            {"user_id": goal["user_id"], "goal_id": goal["goal_id"]},
        ).scalar_one()
        session.execute(
            text(
                """
                insert into plan_adjustments (
                    id, user_id, goal_id, previous_plan_id, new_plan_id, trigger_type,
                    decision, evidence_json, before_snapshot, after_snapshot, plan_patch,
                    change_summary, rationale_json, status, created_at
                ) values (
                    :id, :user_id, :goal_id, :previous_plan_id, null, 'manual',
                    'advance', '{}', '{}', '{}', :plan_patch,
                    '{}', '{}', 'proposed', :created_at
                )
                """
            ),
            {
                "id": advance_id,
                "user_id": goal["user_id"],
                "goal_id": goal["goal_id"],
                "previous_plan_id": current_plan_id,
                "plan_patch": json.dumps({"unlock_next_nodes": True, "increase_difficulty": 1}),
                "created_at": datetime.utcnow().isoformat(),
            },
        )
        session.commit()

    advance_response = client.post(
        "/api/plans/adjustments/adjustment-test-advance/apply",
        json={"user_id": goal["user_id"], "goal_id": goal["goal_id"]},
    )
    assert advance_response.status_code == 200
    advanced = advance_response.json()
    assert any(task["task_type"] == "practice" for task in advanced["created_tasks"])
