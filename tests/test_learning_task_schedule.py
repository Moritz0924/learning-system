from __future__ import annotations

from datetime import date, timedelta

from backend.app.models import BaselineDiagnostic, LearningGoal, LearningPlan, LearningStateSnapshot, PlanTask, User
from backend.app.services.curriculum import ensure_curriculum_seeded
from backend.app.services.learning import get_today_tasks


def _task(task_id: str, *, plan_id: str, user_id: str, goal_id: str, scheduled_day: int, scheduled_date: date) -> PlanTask:
    return PlanTask(
        id=task_id,
        plan_id=plan_id,
        user_id=user_id,
        goal_id=goal_id,
        knowledge_node_id="node-python_foundations",
        knowledge_node_code=f"node_{task_id}",
        title=f"Task {task_id}",
        task_type="study",
        objective="Use the actual scheduled date for today's task list.",
        scheduled_date=scheduled_date,
        scheduled_day=scheduled_day,
        estimated_minutes=30,
        priority=1,
        status="pending",
        payload={},
        origin="planner",
    )


def test_today_tasks_uses_scheduled_date_not_only_day_one(db_session):
    today = date.today()
    user = User(id="schedule-user", email="schedule@example.test", display_name="Schedule User")
    db_session.add(user)
    db_session.flush()
    goal = LearningGoal(
        id="schedule-goal",
        user_id=user.id,
        title="Learn AI apps",
        target_outcome="Ship a reliable tutor",
        deadline=today + timedelta(days=30),
        weekly_hours_target=8,
        learning_preferences={},
    )
    db_session.add(goal)
    db_session.flush()
    ensure_curriculum_seeded(db_session)
    diagnostic = BaselineDiagnostic(
        id="diag-schedule",
        user_id=user.id,
        goal_id=goal.id,
        submitted_answers={},
        baseline_summary="Schedule fixture",
        entry_node_id="node-python_foundations",
        knowledge_gaps=[],
        initial_mastery={},
        evidence_json={},
    )
    db_session.add(diagnostic)
    db_session.flush()
    old_plan = LearningPlan(
        id="schedule-old-plan",
        user_id=user.id,
        goal_id=goal.id,
        version=1,
        status="replaced",
        generated_by="planner",
        rationale_json={},
        valid_from=today - timedelta(days=7),
        valid_to=today + timedelta(days=7),
        plan_json={},
    )
    active_plan = LearningPlan(
        id="schedule-active-plan",
        user_id=user.id,
        goal_id=goal.id,
        version=2,
        status="active",
        generated_by="planner",
        rationale_json={},
        valid_from=today,
        valid_to=today + timedelta(days=14),
        plan_json={},
    )
    db_session.add_all([old_plan, active_plan])
    db_session.flush()
    snapshot = LearningStateSnapshot(
        id="schedule-snapshot",
        user_id=user.id,
        goal_id=goal.id,
        active_plan_id=active_plan.id,
        active_plan_version=active_plan.version,
        baseline_diagnostic_id="diag-schedule",
        mastery_summary={},
        current_state={},
        generated_from={},
    )
    db_session.add_all(
        [
            snapshot,
            _task(
                "old-plan-today",
                plan_id=old_plan.id,
                user_id=user.id,
                goal_id=goal.id,
                scheduled_day=1,
                scheduled_date=today,
            ),
            _task(
                "active-today-day-two",
                plan_id=active_plan.id,
                user_id=user.id,
                goal_id=goal.id,
                scheduled_day=2,
                scheduled_date=today,
            ),
            _task(
                "active-tomorrow",
                plan_id=active_plan.id,
                user_id=user.id,
                goal_id=goal.id,
                scheduled_day=3,
                scheduled_date=today + timedelta(days=1),
            ),
        ]
    )
    db_session.commit()

    payload = get_today_tasks(db_session, user_id=user.id, goal_id=goal.id)

    assert [task["id"] for task in payload["tasks"]] == ["active-today-day-two"]
