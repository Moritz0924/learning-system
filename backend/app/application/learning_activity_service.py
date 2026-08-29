from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.serialization import (
    _learning_event_to_dict,
    _plan_adjustment_record_to_dict,
    _task_to_dict,
    _learning_session_to_dict,
)
from backend.app.models import (
    LearningEvent,
    LearningGoal,
    LearningSession,
    LearningStateSnapshot,
    PlanAdjustmentRecord,
    PlanTask,
)


def load_learning_activity_summary(session: Session, *, user_id: str, goal_id: str) -> dict:
    return {
        "recent_learning_events": _recent_learning_events(session, user_id=user_id, goal_id=goal_id),
        "completion_rate_7d": _completion_rate_7d(session, user_id=user_id, goal_id=goal_id),
    }

def load_plan_adjustment(session: Session, adjustment_id: str | None) -> dict | None:
    if not adjustment_id:
        return None
    record = session.get(PlanAdjustmentRecord, adjustment_id)
    return _plan_adjustment_record_to_dict(record) if record else None

def _load_task_for_user(session: Session, *, user_id: str, task_id: str) -> PlanTask:
    task = session.get(PlanTask, task_id)
    if task is None or task.user_id != user_id:
        raise LookupError(f"task {task_id} not found")
    return task

def _load_goal_for_user(session: Session, *, user_id: str, goal_id: str) -> LearningGoal:
    goal = session.scalar(
        select(LearningGoal).where(
            LearningGoal.id == goal_id,
            LearningGoal.user_id == user_id,
        )
    )
    if goal is None:
        raise LookupError(f"learning goal {goal_id} not found")
    return goal

def _record_learning_event(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    session_id: str | None,
    task_id: str | None,
    event_type: str,
    source: str,
    event_payload: dict,
) -> LearningEvent:
    record = LearningEvent(
        id=f"event-{uuid4()}",
        user_id=user_id,
        goal_id=goal_id,
        session_id=session_id,
        task_id=task_id,
        event_type=event_type,
        source=source,
        event_payload=event_payload,
        occurred_at=datetime.utcnow(),
    )
    session.add(record)
    session.flush()
    return record

def _recent_learning_events(session: Session, *, user_id: str, goal_id: str, limit: int = 5) -> list[dict]:
    events = list(
        session.scalars(
            select(LearningEvent)
            .where(LearningEvent.user_id == user_id, LearningEvent.goal_id == goal_id)
            .order_by(LearningEvent.occurred_at.desc())
            .limit(limit)
        )
    )
    return [_learning_event_to_dict(event) for event in reversed(events)]

def _completion_rate_7d(session: Session, *, user_id: str, goal_id: str) -> float | None:
    tasks = session.scalars(
        select(PlanTask).where(
            PlanTask.user_id == user_id,
            PlanTask.goal_id == goal_id,
            PlanTask.scheduled_day <= 7,
        )
    ).all()
    observed_statuses = {"completed", "done", "missed", "skipped", "incomplete", "failed"}
    observed = [task for task in tasks if (task.status or "").lower() in observed_statuses]
    if not observed:
        return None
    completed = sum(1 for task in observed if (task.status or "").lower() in {"completed", "done"})
    return round(completed / len(observed), 4)

def _refresh_activity_state(session: Session, *, user_id: str, goal_id: str) -> None:
    snapshot = _load_snapshot(session, user_id=user_id, goal_id=goal_id)
    if snapshot is None:
        return
    state = dict(snapshot.current_state or {})
    state.update(load_learning_activity_summary(session, user_id=user_id, goal_id=goal_id))
    snapshot.current_state = state
    session.flush()


def _load_snapshot(session: Session, *, user_id: str, goal_id: str) -> LearningStateSnapshot | None:
    return session.scalar(
        select(LearningStateSnapshot).where(
            LearningStateSnapshot.user_id == user_id,
            LearningStateSnapshot.goal_id == goal_id,
        )
    )
