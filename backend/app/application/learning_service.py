from __future__ import annotations

from datetime import datetime
from math import floor
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import TutorRunRequest
from backend.app.application.engine import _resolve_tutor_request_thread, _run_engine
from backend.app.application.learning_activity_service import (
    _load_task_for_user,
    _record_learning_event,
    _refresh_activity_state,
)
from backend.app.application.serialization import (
    _learning_session_to_dict,
    _plan_adjustment_model_to_dict,
    _task_to_dict,
)
from backend.app.models import LearningGoal, LearningSession, LearningStateSnapshot, PlanTask
from backend.app.core.exceptions import TaskCompletionInProgress, TaskNotStarted, TaskStateConflict


def start_task(
    session: Session,
    *,
    user_id: str,
    task_id: str,
) -> dict:
    task = _load_task_for_user(session, user_id=user_id, task_id=task_id)
    goal = session.scalar(
        select(LearningGoal).where(
            LearningGoal.id == task.goal_id,
            LearningGoal.user_id == user_id,
        ).with_for_update()
    )
    if goal is None:
        raise LookupError(f"learning goal {task.goal_id} not found")
    snapshot = session.scalar(
        select(LearningStateSnapshot).where(
            LearningStateSnapshot.user_id == user_id,
            LearningStateSnapshot.goal_id == task.goal_id,
        )
    )
    if snapshot is None or task.plan_id != snapshot.active_plan_id:
        session.rollback()
        raise TaskStateConflict(
            "task.not_active_plan",
            f"task {task_id} is not part of the active learning plan",
        )
    claimed = session.execute(
        update(PlanTask)
        .where(
            PlanTask.id == task_id,
            PlanTask.user_id == user_id,
            PlanTask.status == "pending",
        )
        .values(status="active")
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        session.rollback()
        session.expire_all()
        task = _load_task_for_user(session, user_id=user_id, task_id=task_id)
    if task.status in {"completed", "done"}:
        completed_session = session.scalar(
            select(LearningSession)
            .where(
                LearningSession.user_id == user_id,
                LearningSession.task_id == task_id,
                LearningSession.status == "completed",
            )
            .order_by(LearningSession.ended_at.desc(), LearningSession.id.desc())
        )
        if completed_session is None:
            raise TaskStateConflict(
                "task.state_conflict",
                f"completed task {task_id} has no completed learning session",
            )
        return {"task": _task_to_dict(task), "session": _learning_session_to_dict(completed_session)}
    if claimed.rowcount != 1 and task.status == "completing":
        raise TaskCompletionInProgress()
    if claimed.rowcount != 1 and task.status == "active":
        active_session = session.scalar(
            select(LearningSession).where(
                LearningSession.user_id == user_id,
                LearningSession.task_id == task_id,
                LearningSession.status == "active",
            )
        )
        if active_session is None:
            raise TaskStateConflict(
                "task.state_conflict",
                f"active task {task_id} has no active learning session",
            )
        return {"task": _task_to_dict(task), "session": _learning_session_to_dict(active_session)}
    if claimed.rowcount != 1:
        raise TaskStateConflict("task.state_conflict", f"task {task_id} cannot be started from {task.status}")

    active_session = session.scalar(
        select(LearningSession).where(
            LearningSession.user_id == user_id,
            LearningSession.task_id == task_id,
            LearningSession.status == "active",
        )
    )
    if active_session is not None:
        session.rollback()
        raise TaskStateConflict(
            "task.state_conflict",
            f"pending task {task_id} already has an active learning session",
        )
    active_session = LearningSession(
        id=f"session-{uuid4()}",
        user_id=user_id,
        goal_id=task.goal_id,
        plan_id=task.plan_id,
        task_id=task.id,
        started_at=datetime.utcnow(),
        duration_minutes=0,
        status="active",
        evidence_json={},
    )
    session.add(active_session)
    task.status = "active"
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        task = _load_task_for_user(session, user_id=user_id, task_id=task_id)
        active_session = session.scalar(
            select(LearningSession).where(
                LearningSession.user_id == user_id,
                LearningSession.task_id == task_id,
                LearningSession.status == "active",
            )
        )
        if active_session is None:
            raise
        return {"task": _task_to_dict(task), "session": _learning_session_to_dict(active_session)}
    _record_learning_event(
        session,
        user_id=user_id,
        goal_id=task.goal_id,
        task_id=task.id,
        session_id=active_session.id,
        event_type="task_started",
        source="task_api",
        event_payload={"plan_id": task.plan_id, "task_title": task.title},
    )
    _refresh_activity_state(session, user_id=user_id, goal_id=task.goal_id)
    session.commit()
    return {"task": _task_to_dict(task), "session": _learning_session_to_dict(active_session)}

def complete_task(
    session: Session,
    *,
    user_id: str,
    task_id: str,
    duration_minutes: int | None,
    evidence: dict,
) -> dict:
    del duration_minutes
    task = _load_task_for_user(session, user_id=user_id, task_id=task_id)
    if task.status in {"completed", "done"}:
        completed_session = session.scalar(
            select(LearningSession)
            .where(
                LearningSession.user_id == user_id,
                LearningSession.task_id == task_id,
                LearningSession.status == "completed",
            )
            .order_by(LearningSession.ended_at.desc(), LearningSession.id.desc())
        )
        if completed_session is not None:
            return {
                "task": _task_to_dict(task),
                "session": _learning_session_to_dict(completed_session),
                "observer_decision": None,
                "plan_adjustment": None,
            }
        raise TaskStateConflict(
            "task.state_conflict",
            f"completed task {task_id} has no completed learning session",
        )
    if task.status == "completing":
        raise TaskCompletionInProgress()
    if task.status == "pending":
        raise TaskNotStarted()
    if task.status != "active":
        raise TaskStateConflict("task.state_conflict", f"task {task_id} cannot be completed from {task.status}")
    active_session = session.scalar(
        select(LearningSession).where(
            LearningSession.user_id == user_id,
            LearningSession.task_id == task_id,
            LearningSession.status == "active",
        )
    )
    if active_session is None:
        raise TaskNotStarted()
    claimed = session.execute(
        update(PlanTask)
        .where(
            PlanTask.id == task_id,
            PlanTask.user_id == user_id,
            PlanTask.status == "active",
        )
        .values(status="completing")
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        session.rollback()
        task = _load_task_for_user(session, user_id=user_id, task_id=task_id)
        completed_session = session.scalar(
            select(LearningSession)
            .where(
                LearningSession.user_id == user_id,
                LearningSession.task_id == task_id,
                LearningSession.status == "completed",
            )
            .order_by(LearningSession.ended_at.desc(), LearningSession.id.desc())
        )
        if task.status in {"completed", "done"} and completed_session is not None:
            return {
                "task": _task_to_dict(task),
                "session": _learning_session_to_dict(completed_session),
                "observer_decision": None,
                "plan_adjustment": None,
            }
        if task.status == "completing":
            raise TaskCompletionInProgress()
        if task.status == "pending":
            raise TaskNotStarted()
        raise TaskStateConflict("task.state_conflict", f"task {task_id} cannot be completed from {task.status}")
    task.status = "completing"
    active_session = session.scalar(
        select(LearningSession).where(
            LearningSession.user_id == user_id,
            LearningSession.task_id == task_id,
            LearningSession.status == "active",
        )
    )
    if active_session is None:
        session.rollback()
        raise TaskNotStarted()
    request = _resolve_tutor_request_thread(
        session,
        TutorRunRequest(
            trigger_type="task_completed",
            user_id=user_id,
            goal_id=task.goal_id,
            thread_id=f"task-{task.id}",
            task_id=task.id,
        ),
    )

    ended_at = datetime.utcnow()
    elapsed_seconds = max(0, floor((ended_at - active_session.started_at).total_seconds()))
    measured_evidence = {
        **evidence,
        "measurement_source": "server_session_clock",
        "duration_seconds": elapsed_seconds,
        "duration_minutes": elapsed_seconds // 60,
        "started_at": active_session.started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
    }
    active_session.ended_at = ended_at
    active_session.duration_minutes = elapsed_seconds // 60
    active_session.status = "completed"
    active_session.evidence_json = measured_evidence
    task.status = "completed"
    _record_learning_event(
        session,
        user_id=user_id,
        goal_id=task.goal_id,
        task_id=task.id,
        session_id=active_session.id,
        event_type="task_completed",
        source="task_api",
        event_payload={
            "plan_id": task.plan_id,
            "task_title": task.title,
            "duration_minutes": active_session.duration_minutes,
            "evidence": measured_evidence,
        },
    )
    _refresh_activity_state(session, user_id=user_id, goal_id=task.goal_id)
    try:
        result = _run_engine(
            session,
            request,
        )
    except Exception:
        session.rollback()
        session.execute(
            update(PlanTask)
            .where(
                PlanTask.id == task_id,
                PlanTask.user_id == user_id,
                PlanTask.status == "completing",
            )
            .values(status="active")
            .execution_options(synchronize_session=False)
        )
        session.commit()
        raise
    session.commit()
    return {
        "task": _task_to_dict(task),
        "session": _learning_session_to_dict(active_session),
        "observer_decision": result.observer_decision.model_dump() if result.observer_decision else None,
        "plan_adjustment": _plan_adjustment_model_to_dict(result.plan_adjustment) if result.plan_adjustment else None,
    }
