from __future__ import annotations

from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import TutorRunRequest
from backend.app.application.engine import _run_engine
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
    _load_goal_for_user(session, user_id=user_id, goal_id=goal_id)
    result = _run_engine(
        session,
        TutorRunRequest(
            trigger_type="chat",
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            user_message=message,
        ),
    )
    session.commit()
    return _run_result_to_dict(result)
