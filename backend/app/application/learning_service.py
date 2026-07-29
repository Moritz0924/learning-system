from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import TutorRunRequest
from backend.app.application.engine import _resolve_tutor_request_thread, _run_engine
from backend.app.application.learning_activity_service import (
    _elapsed_minutes,
    _load_task_for_user,
    _record_learning_event,
    _refresh_activity_state,
)
from backend.app.application.serialization import (
    _learning_session_to_dict,
    _plan_adjustment_model_to_dict,
    _task_to_dict,
)
from backend.app.models import LearningSession, PlanTask


def start_task(
    session: Session,
    *,
    user_id: str,
    task_id: str,
) -> dict:
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
            raise RuntimeError(f"completed task {task_id} has no completed learning session")
        return {"task": _task_to_dict(task), "session": _learning_session_to_dict(completed_session)}
    active_session = session.scalar(
        select(LearningSession).where(
            LearningSession.user_id == user_id,
            LearningSession.task_id == task_id,
            LearningSession.status == "active",
        )
    )
    should_record_started_event = active_session is None or task.status != "active"
    created_active_session = active_session is None
    if active_session is None:
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
    if task.status not in {"completed", "done"}:
        task.status = "active"
    try:
        session.flush()
    except IntegrityError:
        if not created_active_session:
            raise
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
    if should_record_started_event:
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
    task = _load_task_for_user(session, user_id=user_id, task_id=task_id)
    request = _resolve_tutor_request_thread(
        session,
        TutorRunRequest(
            trigger_type="task_completed",
            user_id=user_id,
            goal_id=task.goal_id,
            thread_id=f"task-{task.id}",
            metadata={"task_id": task.id},
        ),
    )
    claimed = session.execute(
        update(PlanTask)
        .where(
            PlanTask.id == task_id,
            PlanTask.user_id == user_id,
            ~PlanTask.status.in_({"completed", "done", "completing"}),
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
        raise RuntimeError(f"task {task_id} completion is already in progress")
    task.status = "completing"
    active_session = session.scalar(
        select(LearningSession).where(
            LearningSession.user_id == user_id,
            LearningSession.task_id == task_id,
            LearningSession.status == "active",
        )
    )
    if active_session is None:
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
        session.flush()

    ended_at = datetime.utcnow()
    active_session.ended_at = ended_at
    active_session.duration_minutes = duration_minutes or _elapsed_minutes(active_session.started_at, ended_at)
    active_session.status = "completed"
    active_session.evidence_json = evidence
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
            "evidence": evidence,
        },
    )
    _refresh_activity_state(session, user_id=user_id, goal_id=task.goal_id)
    result = _run_engine(
        session,
        request,
    )
    session.commit()
    return {
        "task": _task_to_dict(task),
        "session": _learning_session_to_dict(active_session),
        "observer_decision": result.observer_decision.model_dump() if result.observer_decision else None,
        "plan_adjustment": _plan_adjustment_model_to_dict(result.plan_adjustment) if result.plan_adjustment else None,
    }
