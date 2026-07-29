from __future__ import annotations

from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import TutorRunRequest
from backend.app.domain.memory import MemoryCandidate
from backend.app.application.engine import _prepare_tutor_context, _run_engine
from backend.app.application.conversation_service import ConversationService
from backend.app.application.learning_activity_service import _load_goal_for_user
from backend.app.application.serialization import _run_result_to_dict


def answer_tutor_question(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    thread_id: str,
    message: str,
    memory_candidate: MemoryCandidate | None = None,
) -> dict:
    request = TutorRunRequest(
        trigger_type="chat",
        user_id=user_id,
        goal_id=goal_id,
        thread_id=thread_id,
        user_message=message,
        memory_candidates=[] if memory_candidate is None else [memory_candidate],
    )
    try:
        _load_goal_for_user(session, user_id=user_id, goal_id=goal_id)
        prepared_context = _prepare_tutor_context(session, request)
        session.rollback()
        thread = ConversationService(session).ensure_legacy_thread(
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
        )
        session.commit()
        request = request.model_copy(update={"thread_id": thread.id})
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
