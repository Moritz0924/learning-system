from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    BaselineDiagnostic,
    LearnerProfile,
    LearningGoal,
    LearningPlan,
    LearningStateSnapshot,
    MasteryRecord,
    PlanTask,
    User,
)
from backend.app.services.curriculum import ensure_curriculum_seeded
from backend.app.services.diagnosis import build_baseline_diagnosis
from backend.app.services.stage3 import load_learning_activity_summary, load_plan_adjustment


@dataclass(frozen=True)
class DiagnosisSubmissionResult:
    baseline_diagnostic_id: str
    entry_node_id: str
    entry_node_code: str
    baseline_summary: str
    knowledge_gaps: list[dict]
    initial_mastery: dict
    evidence_json: dict
    active_plan_id: str
    active_plan_version: int


class NotFoundError(LookupError):
    pass


def create_goal(
    session: Session,
    *,
    user_id: str | None,
    email: str | None,
    display_name: str | None,
    title: str,
    target_outcome: str,
    deadline: str | date | None,
    weekly_hours_target: int,
    learning_preferences: dict,
    available_slots: dict | None = None,
) -> LearningGoal:
    user_id = user_id or f"user-{uuid4()}"
    user = session.get(User, user_id)
    if user is None:
        user = User(
            id=user_id,
            email=email or f"{user_id}@example.test",
            display_name=display_name or "Learner",
            status="active",
        )
        session.add(user)
    profile = session.get(LearnerProfile, user_id)
    if profile is None:
        session.add(
            LearnerProfile(
                user_id=user_id,
                weekly_hours=weekly_hours_target,
                available_slots=available_slots or {},
                learning_preferences=learning_preferences,
                privacy_settings={"data_scope": "v1_demo"},
            )
        )
    else:
        profile.weekly_hours = weekly_hours_target
        profile.available_slots = available_slots or profile.available_slots
        profile.learning_preferences = learning_preferences

    parsed_deadline = date.fromisoformat(deadline) if isinstance(deadline, str) else deadline
    goal = LearningGoal(
        id=f"goal-{uuid4()}",
        user_id=user_id,
        title=title,
        domain="ai_app_dev",
        target_outcome=target_outcome,
        deadline=parsed_deadline,
        weekly_hours_target=weekly_hours_target,
        status="active",
        learning_preferences=learning_preferences,
    )
    session.add(goal)
    session.commit()
    return goal


def submit_onboarding_diagnosis(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    self_assessment: dict,
    submitted_answers: dict,
) -> DiagnosisSubmissionResult:
    _load_goal_for_user(session, user_id=user_id, goal_id=goal_id)
    curriculum = ensure_curriculum_seeded(session)
    result = build_baseline_diagnosis(
        session,
        curriculum_id=curriculum.id,
        self_assessment=self_assessment,
        submitted_answers=submitted_answers,
    )
    diagnostic = BaselineDiagnostic(
        id=f"diag-{uuid4()}",
        user_id=user_id,
        goal_id=goal_id,
        submitted_answers=submitted_answers,
        baseline_summary=result.baseline_summary,
        entry_node_id=result.entry_node_id,
        knowledge_gaps=result.knowledge_gaps,
        initial_mastery=result.initial_mastery,
        evidence_json=result.evidence_json,
    )
    session.add(diagnostic)
    version = _next_plan_version(session, user_id, goal_id)
    plan = LearningPlan(
        id=f"plan-{uuid4()}",
        user_id=user_id,
        goal_id=goal_id,
        curriculum_id=curriculum.id,
        version=version,
        status="active",
        generated_by="planner",
        rationale_json={"source": "stage1-diagnosis", "entry_node_code": result.entry_node_code},
        valid_from=date.today(),
        valid_to=date.today() + timedelta(days=14),
        plan_json={"entry_node_code": result.entry_node_code, "horizon_days": 14},
    )
    session.add(plan)
    session.flush()

    first_gap = result.knowledge_gaps[0] if result.knowledge_gaps else None
    node_code = first_gap["node_code"] if first_gap else result.entry_node_code
    node_id = first_gap["node_id"] if first_gap else result.entry_node_id
    session.add(
        PlanTask(
            id=f"task-{uuid4()}",
            plan_id=plan.id,
            user_id=user_id,
            goal_id=goal_id,
            knowledge_node_id=node_id,
            knowledge_node_code=node_code,
            title=f"Study {node_code}",
            task_type="study",
            objective=f"Build confidence on {node_code.replace('_', ' ')}.",
            scheduled_date=date.today(),
            scheduled_day=1,
            payload={"source": "stage1-diagnosis"},
            priority=1,
            origin="planner",
        )
    )

    for node_code, item in result.initial_mastery.items():
        existing = session.scalar(
            select(MasteryRecord).where(
                MasteryRecord.user_id == user_id,
                MasteryRecord.goal_id == goal_id,
                MasteryRecord.knowledge_node_id == item["knowledge_node_id"],
            )
        )
        if existing is None:
            session.add(
                MasteryRecord(
                    id=f"mastery-{uuid4()}",
                    user_id=user_id,
                    goal_id=goal_id,
                    knowledge_node_id=item["knowledge_node_id"],
                    mastery_score=item["score"],
                    confidence=item["confidence"],
                    evidence_count=1,
                    source_breakdown={"baseline": item["score"], "node_code": node_code},
                )
            )
        else:
            existing.mastery_score = item["score"]
            existing.confidence = item["confidence"]
            existing.evidence_count += 1

    snapshot = session.scalar(
        select(LearningStateSnapshot).where(
            LearningStateSnapshot.user_id == user_id,
            LearningStateSnapshot.goal_id == goal_id,
        )
    )
    snapshot_payload = {
        "today_tasks": [{"knowledge_node_code": node_code, "title": f"Study {node_code}"}],
        "next_action": "study",
        "review_queue": [],
    }
    generated_from = {"baseline_diagnostic_id": diagnostic.id, "active_plan_id": plan.id}
    if snapshot is None:
        snapshot = LearningStateSnapshot(
            id=f"snapshot-{uuid4()}",
            user_id=user_id,
            goal_id=goal_id,
            active_plan_id=plan.id,
            active_plan_version=plan.version,
            baseline_diagnostic_id=diagnostic.id,
            mastery_summary=result.initial_mastery,
            current_state=snapshot_payload,
            generated_from=generated_from,
        )
        session.add(snapshot)
    else:
        snapshot.active_plan_id = plan.id
        snapshot.active_plan_version = plan.version
        snapshot.baseline_diagnostic_id = diagnostic.id
        snapshot.mastery_summary = result.initial_mastery
        snapshot.current_state = snapshot_payload
        snapshot.generated_from = generated_from

    session.commit()
    return DiagnosisSubmissionResult(
        baseline_diagnostic_id=diagnostic.id,
        entry_node_id=result.entry_node_id,
        entry_node_code=result.entry_node_code,
        baseline_summary=result.baseline_summary,
        knowledge_gaps=result.knowledge_gaps,
        initial_mastery=result.initial_mastery,
        evidence_json=result.evidence_json,
        active_plan_id=plan.id,
        active_plan_version=plan.version,
    )


def get_current_state(session: Session, *, user_id: str, goal_id: str) -> dict:
    snapshot = session.scalar(
        select(LearningStateSnapshot).where(
            LearningStateSnapshot.user_id == user_id,
            LearningStateSnapshot.goal_id == goal_id,
        )
    )
    if snapshot is None:
        raise NotFoundError("state snapshot not found")
    goal = session.get(LearningGoal, goal_id)
    tasks_payload = get_today_tasks(session, user_id=user_id, goal_id=goal_id)["tasks"]
    current_state = dict(snapshot.current_state or {})
    current_state.setdefault("review_queue", [])
    current_state.update(load_learning_activity_summary(session, user_id=user_id, goal_id=goal_id))
    latest_adjustment = load_plan_adjustment(session, snapshot.latest_plan_adjustment_id)
    return {
        "user_id": user_id,
        "goal": {"id": goal_id, "title": goal.title if goal else None},
        "active_plan": {"id": snapshot.active_plan_id, "version": snapshot.active_plan_version},
        "baseline_diagnostic": {"id": snapshot.baseline_diagnostic_id},
        "mastery_summary": snapshot.mastery_summary,
        "current_state": current_state,
        "generated_from": snapshot.generated_from,
        "latest_plan_adjustment": latest_adjustment,
        "today_tasks": tasks_payload,
        "updated_at": snapshot.updated_at,
    }


def get_today_tasks(session: Session, *, user_id: str, goal_id: str) -> dict:
    snapshot = session.scalar(
        select(LearningStateSnapshot).where(
            LearningStateSnapshot.user_id == user_id,
            LearningStateSnapshot.goal_id == goal_id,
        )
    )
    task_query = select(PlanTask).where(
        PlanTask.user_id == user_id,
        PlanTask.goal_id == goal_id,
        PlanTask.scheduled_day == 1,
    )
    if snapshot and snapshot.active_plan_id:
        task_query = task_query.where(PlanTask.plan_id == snapshot.active_plan_id)
    tasks = list(
        session.scalars(
            task_query.order_by(PlanTask.priority, PlanTask.id)
        )
    )
    return {
        "user_id": user_id,
        "goal_id": goal_id,
        "tasks": [
            {
                "id": task.id,
                "knowledge_node_code": task.knowledge_node_code,
                "knowledge_node_id": task.knowledge_node_id,
                "knowledge_node_title": task.knowledge_node_code.replace("_", " ").title(),
                "title": task.title,
                "objective": task.objective,
                "task_type": task.task_type,
                "scheduled_date": task.scheduled_date,
                "estimated_minutes": task.estimated_minutes,
                "status": task.status,
            }
            for task in tasks
        ],
    }


def _next_plan_version(session: Session, user_id: str, goal_id: str) -> int:
    versions = session.scalars(
        select(LearningPlan.version).where(
            LearningPlan.user_id == user_id,
            LearningPlan.goal_id == goal_id,
        )
    ).all()
    return max(versions, default=0) + 1


def _load_goal_for_user(session: Session, *, user_id: str, goal_id: str) -> LearningGoal:
    goal = session.scalar(
        select(LearningGoal).where(
            LearningGoal.id == goal_id,
            LearningGoal.user_id == user_id,
        )
    )
    if goal is None:
        raise NotFoundError(f"learning goal {goal_id} not found")
    return goal
