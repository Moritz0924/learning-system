from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import TutorRunRequest
from backend.app.application.engine import _run_engine
from backend.app.application.learning_activity_service import (
    _load_goal_for_user,
    _record_learning_event,
    _refresh_activity_state,
)
from backend.app.application.serialization import _draft_to_dict
from backend.app.core.exceptions import AssessmentSubmissionConflict
from backend.app.infrastructure.persistence.repositories.assessment_repository import (
    refresh_phase_state_after_submit,
    upsert_phase_state,
)
from backend.app.infrastructure.persistence.repositories.state_repository import SQLAlchemyStateRepository
from backend.app.models import Assessment


def create_assessment(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    assessment_type: str,
    knowledge_node_ids: list[str],
) -> dict:
    _load_goal_for_user(session, user_id=user_id, goal_id=goal_id)
    result = _run_engine(
        session,
        TutorRunRequest(
            trigger_type="assessment_due",
            user_id=user_id,
            goal_id=goal_id,
            thread_id="assessment",
            assessment_type=assessment_type,
            knowledge_node_ids=knowledge_node_ids,
        ),
    )
    session.commit()
    if result.assessment_draft is None:
        raise RuntimeError("phase2 engine did not return an assessment draft")
    return _draft_to_dict(result.assessment_draft)

def create_phase_assessment(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    thread_id: str,
    phase_code: str,
    knowledge_node_ids: list[str],
) -> dict:
    _load_goal_for_user(session, user_id=user_id, goal_id=goal_id)
    result = _run_engine(
        session,
        TutorRunRequest(
            trigger_type="assessment_due",
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            assessment_type="phase",
            knowledge_node_ids=knowledge_node_ids,
        ),
    )
    if result.assessment_draft is None:
        raise RuntimeError("phase2 engine did not return a phase assessment draft")
    phase_state = upsert_phase_state(
        session,
        user_id=user_id,
        goal_id=goal_id,
        assessment_id=result.assessment_draft.assessment_id,
        phase_code=phase_code,
        knowledge_node_ids=knowledge_node_ids,
        status="active",
    )
    SQLAlchemyStateRepository(session).refresh_snapshot(
        user_id,
        goal_id,
        {
            "phase_assessment_state_id": phase_state.id,
            "generated_from": {"phase_assessment_state_id": phase_state.id},
        },
    )
    session.commit()
    payload = _draft_to_dict(result.assessment_draft)
    payload["phase_assessment_state_id"] = phase_state.id
    payload["phase_code"] = phase_code
    return payload

def submit_assessment(
    session: Session,
    *,
    assessment_id: str,
    user_id: str,
    answers: dict[str, str],
) -> dict:
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.user_id != user_id:
        raise LookupError(f"assessment {assessment_id} not found")
    claimed = session.execute(
        update(Assessment)
        .where(
            Assessment.id == assessment_id,
            Assessment.user_id == user_id,
            Assessment.status == "active",
        )
        .values(status="submitted")
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        session.rollback()
        raise AssessmentSubmissionConflict(f"assessment {assessment_id} was already submitted")
    assessment.status = "submitted"
    result = _run_engine(
        session,
        TutorRunRequest(
            trigger_type="assessment_submitted",
            user_id=user_id,
            goal_id=assessment.goal_id,
            thread_id="assessment-submit",
            assessment_id=assessment_id,
            submitted_answers=answers,
        ),
    )
    refresh_phase_state_after_submit(session, assessment=assessment, result=result)
    if result.assessment_result is not None:
        _record_learning_event(
            session,
            user_id=user_id,
            goal_id=assessment.goal_id,
            task_id=None,
            session_id=None,
            event_type="assessment_submitted",
            source="assessment",
            event_payload={
                "assessment_id": assessment_id,
                "score": result.assessment_result.score,
                "mastery_updates": [item.model_dump() for item in result.mastery_updates],
            },
        )
        _refresh_activity_state(session, user_id=user_id, goal_id=assessment.goal_id)
    session.commit()
    if result.assessment_result is None:
        raise RuntimeError("phase2 engine did not return an assessment result")
    payload = result.assessment_result.model_dump()
    payload["mastery_updates"] = [item.model_dump() for item in result.mastery_updates]
    payload["observer_decision"] = result.observer_decision.model_dump() if result.observer_decision else None
    return payload
