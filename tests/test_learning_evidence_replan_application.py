from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.application.learning_service import start_task
from backend.app.application.planning_service import apply_plan_adjustment
from backend.app.core.exceptions import PlanApplicationConflict
from backend.app.models import AgentRun, LearningPlan, LearningSession, PlanAdjustmentRecord


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
        headers={"X-User-Id": goal["user_id"]},
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
        headers={"X-User-Id": goal["user_id"]},
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
        headers={"X-User-Id": goal["user_id"]},
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
        headers={"X-User-Id": goal["user_id"]},
        json={"user_id": goal["user_id"]},
    )
    assert start_response.status_code == 200
    started = start_response.json()
    assert started["task"]["status"] == "active"
    assert started["session"]["status"] == "active"
    assert started["session"]["task_id"] == task["id"]

    complete_response = client.post(
        f"/api/tasks/{task['id']}/complete",
        headers={"X-User-Id": goal["user_id"]},
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


def test_assessment_cannot_be_submitted_twice(client, session_factory):
    goal = _create_goal_and_diagnosis(client, user_id="duplicate-submit-user")
    node_id = _state(client, goal)["today_tasks"][0]["knowledge_node_id"]
    created = client.post(
        "/api/assessments",
        headers={"X-User-Id": goal["user_id"]},
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "duplicate-submit-thread",
            "assessment_type": "daily",
            "knowledge_node_ids": [node_id],
        },
    )
    assert created.status_code == 201
    assessment = created.json()
    payload = {
        "user_id": goal["user_id"],
        "answers": {item["item_id"]: "wrong" for item in assessment["items"]},
    }

    first = client.post(
        f"/api/assessments/{assessment['assessment_id']}/submit",
        headers={"X-User-Id": goal["user_id"]},
        json=payload,
    )
    second = client.post(
        f"/api/assessments/{assessment['assessment_id']}/submit",
        headers={"X-User-Id": goal["user_id"]},
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert "already submitted" in second.json()["detail"]
    with session_factory() as session:
        attempt_count = session.execute(
            text("select count(*) from assessment_attempts where assessment_id = :assessment_id"),
            {"assessment_id": assessment["assessment_id"]},
        ).scalar_one()
        event_count = session.execute(
            text("select count(*) from learning_events where event_type = 'assessment_submitted'")
        ).scalar_one()
        assert attempt_count == 1
        assert event_count == 1


def test_engine_failure_rolls_back_business_changes_but_persists_sanitized_audit(
    client,
    session_factory,
    monkeypatch,
):
    import backend.app.application.engine as application_engine

    goal = _create_goal_and_diagnosis(client, user_id="engine-failure-user")
    task = _state(client, goal)["today_tasks"][0]
    started = client.post(
        f"/api/tasks/{task['id']}/start",
        headers={"X-User-Id": goal["user_id"]},
        json={"user_id": goal["user_id"]},
    )
    assert started.status_code == 200

    def fail_engine_run(self, request):
        raise RuntimeError("secret upstream payload must not be persisted")

    monkeypatch.setattr(application_engine.Phase2TutorEngine, "run", fail_engine_run)

    with pytest.raises(RuntimeError, match="secret upstream payload"):
        client.post(
            f"/api/tasks/{task['id']}/complete",
            headers={"X-User-Id": goal["user_id"]},
            json={
                "user_id": goal["user_id"],
                "duration_minutes": 20,
                "evidence": {"note": "This write must roll back."},
            },
        )

    with session_factory() as session:
        stored_session = session.get(LearningSession, started.json()["session"]["id"])
        assert stored_session.status == "active"
        assert stored_session.ended_at is None
        task_status = session.execute(
            text("select status from plan_tasks where id = :task_id"),
            {"task_id": task["id"]},
        ).scalar_one()
        assert task_status == "active"
        assert session.execute(
            text("select count(*) from learning_events where event_type = 'task_completed'")
        ).scalar_one() == 0
        failed_runs = session.query(AgentRun).filter_by(
            user_id=goal["user_id"],
            trigger_type="task_completed",
            status="failed",
        ).all()
        assert len(failed_runs) == 1
        assert failed_runs[0].error_message == "RuntimeError"


def test_start_task_is_idempotent_for_active_session(client, session_factory):
    goal = _create_goal_and_diagnosis(client, user_id="repeat-start-user")
    task = _state(client, goal)["today_tasks"][0]

    first = client.post(
        f"/api/tasks/{task['id']}/start",
        headers={"X-User-Id": goal["user_id"]},
        json={"user_id": goal["user_id"]},
    )
    second = client.post(
        f"/api/tasks/{task['id']}/start",
        headers={"X-User-Id": goal["user_id"]},
        json={"user_id": goal["user_id"]},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["session"]["id"] == first.json()["session"]["id"]

    with session_factory() as session:
        assert session.execute(text("select count(*) from learning_sessions")).scalar_one() == 1
        started_events = session.execute(
            text("select count(*) from learning_events where event_type = 'task_started'")
        ).scalar_one()
        assert started_events == 1


def test_complete_task_is_idempotent_after_task_is_already_completed(client, session_factory):
    goal = _create_goal_and_diagnosis(client, user_id="repeat-complete-user")
    task = _state(client, goal)["today_tasks"][0]
    started = client.post(
        f"/api/tasks/{task['id']}/start",
        headers={"X-User-Id": goal["user_id"]},
        json={"user_id": goal["user_id"]},
    )
    assert started.status_code == 200
    payload = {
        "user_id": goal["user_id"],
        "duration_minutes": 25,
        "evidence": {"note": "Only one completion should be recorded."},
    }

    first = client.post(
        f"/api/tasks/{task['id']}/complete",
        headers={"X-User-Id": goal["user_id"]},
        json=payload,
    )
    second = client.post(
        f"/api/tasks/{task['id']}/complete",
        headers={"X-User-Id": goal["user_id"]},
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["session"]["id"] == first.json()["session"]["id"]
    assert second.json()["observer_decision"] is None
    assert second.json()["plan_adjustment"] is None
    with session_factory() as session:
        assert session.execute(text("select count(*) from learning_sessions")).scalar_one() == 1
        assert session.execute(
            text("select count(*) from learning_events where event_type = 'task_completed'")
        ).scalar_one() == 1


def test_start_task_does_not_reopen_completed_task(client, session_factory):
    goal = _create_goal_and_diagnosis(client, user_id="restart-completed-user")
    task = _state(client, goal)["today_tasks"][0]
    started = client.post(
        f"/api/tasks/{task['id']}/start",
        headers={"X-User-Id": goal["user_id"]},
        json={"user_id": goal["user_id"]},
    )
    completed = client.post(
        f"/api/tasks/{task['id']}/complete",
        headers={"X-User-Id": goal["user_id"]},
        json={
            "user_id": goal["user_id"],
            "duration_minutes": 20,
            "evidence": {"note": "Completed tasks stay completed."},
        },
    )
    restarted = client.post(
        f"/api/tasks/{task['id']}/start",
        headers={"X-User-Id": goal["user_id"]},
        json={"user_id": goal["user_id"]},
    )

    assert started.status_code == 200
    assert completed.status_code == 200
    assert restarted.status_code == 200
    assert restarted.json()["task"]["status"] == "completed"
    assert restarted.json()["session"]["id"] == completed.json()["session"]["id"]
    assert restarted.json()["session"]["status"] == "completed"
    with session_factory() as session:
        assert session.execute(text("select count(*) from learning_sessions")).scalar_one() == 1
        assert session.execute(
            text("select count(*) from learning_events where event_type = 'task_started'")
        ).scalar_one() == 1


def test_start_task_recovers_when_concurrent_request_wins_active_session_insert(
    client,
    session_factory,
    monkeypatch,
):
    goal = _create_goal_and_diagnosis(client, user_id="concurrent-start-user")
    task = _state(client, goal)["today_tasks"][0]
    winner = client.post(
        f"/api/tasks/{task['id']}/start",
        headers={"X-User-Id": goal["user_id"]},
        json={"user_id": goal["user_id"]},
    )
    assert winner.status_code == 200

    with session_factory() as stale_session:
        real_scalar = stale_session.scalar
        hid_active_session_once = False

        def stale_scalar(statement, *args, **kwargs):
            nonlocal hid_active_session_once
            if not hid_active_session_once and "FROM learning_sessions" in str(statement):
                hid_active_session_once = True
                return None
            return real_scalar(statement, *args, **kwargs)

        monkeypatch.setattr(stale_session, "scalar", stale_scalar)
        recovered = start_task(stale_session, user_id=goal["user_id"], task_id=task["id"])

    assert recovered["session"]["id"] == winner.json()["session"]["id"]
    with session_factory() as session:
        assert session.execute(text("select count(*) from learning_sessions")).scalar_one() == 1
        assert session.execute(
            text("select count(*) from learning_events where event_type = 'task_started'")
        ).scalar_one() == 1


def test_replan_preview_then_apply_creates_new_plan_tasks_and_audit_event(client, session_factory):
    goal = _create_goal_and_diagnosis(client, user_id="apply-user")
    before = _state(client, goal)
    old_plan_id = before["active_plan"]["id"]
    old_version = before["active_plan"]["version"]
    node_id = before["today_tasks"][0]["knowledge_node_id"]
    _create_low_score_assessment(client, goal, node_id)

    replan_response = client.post(
        "/api/plans/replan",
        headers={"X-User-Id": goal["user_id"]},
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
        headers={"X-User-Id": goal["user_id"]},
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


def test_stale_transaction_cannot_apply_the_same_plan_adjustment_twice(client, session_factory):
    goal = _create_goal_and_diagnosis(client, user_id="stale-adjustment-user")
    node_id = _state(client, goal)["today_tasks"][0]["knowledge_node_id"]
    _create_low_score_assessment(client, goal, node_id)
    proposed_response = client.post(
        "/api/plans/replan",
        headers={"X-User-Id": goal["user_id"]},
        json={
            "user_id": goal["user_id"],
            "goal_id": goal["goal_id"],
            "thread_id": "stale-adjustment-thread",
            "message": "Please add focused review before continuing.",
        },
    )
    assert proposed_response.status_code == 200
    adjustment_id = proposed_response.json()["adjustment_id"]

    with session_factory() as stale_session:
        stale_record = stale_session.get(PlanAdjustmentRecord, adjustment_id)
        assert stale_record.status == "proposed"

        first_apply = client.post(
            f"/api/plans/adjustments/{adjustment_id}/apply",
            headers={"X-User-Id": goal["user_id"]},
            json={"user_id": goal["user_id"], "goal_id": goal["goal_id"]},
        )
        assert first_apply.status_code == 200

        with pytest.raises(PlanApplicationConflict, match="no longer proposed"):
            apply_plan_adjustment(
                stale_session,
                adjustment_id=adjustment_id,
                user_id=goal["user_id"],
                goal_id=goal["goal_id"],
            )

    with session_factory() as session:
        plans = session.query(LearningPlan).filter_by(user_id=goal["user_id"], goal_id=goal["goal_id"]).all()
        assert len(plans) == 2
        assert sum(plan.status == "active" for plan in plans) == 1
        event_count = session.execute(
            text("select count(*) from learning_events where event_type = 'plan_adjustment_applied'")
        ).scalar_one()
        assert event_count == 1


def test_plan_adjustment_locks_goal_before_snapshot(client):
    goal = _create_goal_and_diagnosis(client, user_id="goal-lock-order-user")
    node_id = _state(client, goal)["today_tasks"][0]["knowledge_node_id"]
    _create_low_score_assessment(client, goal, node_id)
    proposed = client.post(
        "/api/plans/replan",
        headers={"X-User-Id": goal["user_id"]},
        json={
            "goal_id": goal["goal_id"],
            "thread_id": "goal-lock-order-thread",
            "message": "Please add focused review before continuing.",
        },
    ).json()
    locked_selects: list[str] = []

    def capture_for_update(execute_state) -> None:
        statement = execute_state.statement
        if execute_state.is_select and getattr(statement, "_for_update_arg", None) is not None:
            locked_selects.append(str(statement).lower())

    event.listen(Session, "do_orm_execute", capture_for_update)
    try:
        response = client.post(
            f"/api/plans/adjustments/{proposed['adjustment_id']}/apply",
            headers={"X-User-Id": goal["user_id"]},
            json={"goal_id": goal["goal_id"]},
        )
    finally:
        event.remove(Session, "do_orm_execute", capture_for_update)

    assert response.status_code == 200
    assert "learning_goals" in locked_selects[0]
    assert any("learning_state_snapshots" in statement for statement in locked_selects[1:])


def test_plan_adjustment_based_on_replaced_plan_cannot_be_applied(client, session_factory):
    goal = _create_goal_and_diagnosis(client, user_id="stale-plan-proposal-user")
    node_id = _state(client, goal)["today_tasks"][0]["knowledge_node_id"]
    _create_low_score_assessment(client, goal, node_id)

    proposals = []
    for thread_id in ("proposal-one", "proposal-two"):
        response = client.post(
            "/api/plans/replan",
            headers={"X-User-Id": goal["user_id"]},
            json={
                "user_id": goal["user_id"],
                "goal_id": goal["goal_id"],
                "thread_id": thread_id,
                "message": "Please add focused review before continuing.",
            },
        )
        assert response.status_code == 200
        proposals.append(response.json())
    assert proposals[0]["previous_plan_id"] == proposals[1]["previous_plan_id"]

    first = client.post(
        f"/api/plans/adjustments/{proposals[0]['adjustment_id']}/apply",
        headers={"X-User-Id": goal["user_id"]},
        json={"user_id": goal["user_id"], "goal_id": goal["goal_id"]},
    )
    second = client.post(
        f"/api/plans/adjustments/{proposals[1]['adjustment_id']}/apply",
        headers={"X-User-Id": goal["user_id"]},
        json={"user_id": goal["user_id"], "goal_id": goal["goal_id"]},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert "active plan has changed" in second.json()["detail"]
    with session_factory() as session:
        plans = session.query(LearningPlan).filter_by(user_id=goal["user_id"], goal_id=goal["goal_id"]).all()
        assert len(plans) == 2
        assert sum(plan.status == "active" for plan in plans) == 1


def test_create_all_schema_enforces_unique_plan_version(session_factory):
    with session_factory() as session:
        common = {
            "user_id": "constraint-user",
            "goal_id": "constraint-goal",
            "curriculum_id": None,
            "version": 1,
            "status": "active",
            "generated_by": "test",
            "rationale_json": {},
            "valid_from": datetime.utcnow().date(),
            "valid_to": datetime.utcnow().date(),
            "plan_json": {},
        }
        session.add_all(
            [
                LearningPlan(id="constraint-plan-1", **common),
                LearningPlan(id="constraint-plan-2", **common),
            ]
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_repeated_diagnosis_replaces_previous_active_plan(client, session_factory):
    goal = _create_goal_and_diagnosis(client, user_id="repeat-diagnosis-user")
    second = client.post(
        "/api/onboarding/diagnosis",
        headers={"X-User-Id": goal["user_id"]},
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

    assert second.status_code == 201
    assert second.json()["active_plan_version"] == 2
    with session_factory() as session:
        plans = session.query(LearningPlan).filter_by(user_id=goal["user_id"], goal_id=goal["goal_id"]).all()
        assert len(plans) == 2
        assert sum(plan.status == "active" for plan in plans) == 1
        assert {plan.status for plan in plans} == {"active", "replaced"}


def test_create_all_schema_enforces_single_active_plan_per_goal(session_factory):
    with session_factory() as session:
        common = {
            "user_id": "active-plan-user",
            "goal_id": "active-plan-goal",
            "curriculum_id": None,
            "status": "active",
            "generated_by": "test",
            "rationale_json": {},
            "valid_from": datetime.utcnow().date(),
            "valid_to": datetime.utcnow().date(),
            "plan_json": {},
        }
        session.add_all(
            [
                LearningPlan(id="active-plan-1", version=1, **common),
                LearningPlan(id="active-plan-2", version=2, **common),
            ]
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_create_all_schema_enforces_single_active_session_per_task(session_factory):
    with session_factory() as session:
        common = {
            "user_id": "active-session-user",
            "goal_id": "active-session-goal",
            "plan_id": "active-session-plan",
            "task_id": "active-session-task",
            "started_at": datetime.utcnow(),
            "duration_minutes": 0,
            "status": "active",
            "evidence_json": {},
        }
        session.add_all(
            [
                LearningSession(id="active-session-1", **common),
                LearningSession(id="active-session-2", **common),
            ]
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_keep_adjustment_cannot_be_applied(client):
    goal = _create_goal_and_diagnosis(client, user_id="keep-user")

    replan_response = client.post(
        "/api/plans/replan",
        headers={"X-User-Id": goal["user_id"]},
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
        headers={"X-User-Id": goal["user_id"]},
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
        headers={"X-User-Id": goal["user_id"]},
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
        headers={"X-User-Id": goal["user_id"]},
        json={"user_id": goal["user_id"], "goal_id": goal["goal_id"]},
    )
    assert advance_response.status_code == 200
    advanced = advance_response.json()
    assert any(task["task_type"] == "practice" for task in advanced["created_tasks"])
