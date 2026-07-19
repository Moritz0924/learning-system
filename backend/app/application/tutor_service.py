from __future__ import annotations

from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import TutorRunRequest
from backend.app.application.engine import _prepare_tutor_context, _run_engine
from backend.app.application.learning_activity_service import _load_goal_for_user
from backend.app.application.serialization import _run_result_to_dict


def answer_tutor_question(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    thread_id: str,
    message: str,
) -> dict:
    request = TutorRunRequest(
        trigger_type="chat",
        user_id=user_id,
        goal_id=goal_id,
        thread_id=thread_id,
        user_message=message,
    )
    try:
        _load_goal_for_user(session, user_id=user_id, goal_id=goal_id)
        prepared_context = _prepare_tutor_context(session, request)
        session.rollback()
        result = _run_engine(
            session,
            request,
            prepared_context=prepared_context,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return _run_result_to_dict(result)
