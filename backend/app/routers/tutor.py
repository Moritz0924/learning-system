from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.application.tutor_service import answer_tutor_question
from backend.app.api.schemas.tutor import (
    LearningPreferenceDeclaration,
    LongTermGoalDeclaration,
    TutorChatRequest,
)
from backend.app.application.memory_candidate_service import (
    build_explicit_goal_candidate,
    build_explicit_preference_candidate,
)
from backend.app.domain.memory import MemoryGateInvariantError, MemoryIdempotencyConflict
from backend.app.domain.conversation import ConversationError


router = APIRouter(prefix="/api/tutor", tags=["tutor"])


@router.post("/chat")
def tutor_chat_endpoint(
    payload: TutorChatRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    try:
        memory_candidate = None
        if isinstance(payload.memory_declaration, LearningPreferenceDeclaration):
            memory_candidate = build_explicit_preference_candidate(
                user_id=principal.user_id,
                request_id=payload.memory_declaration.request_id,
                preference_key=payload.memory_declaration.preference_key,
                preference_value=payload.memory_declaration.preference_value,
            )
        elif isinstance(payload.memory_declaration, LongTermGoalDeclaration):
            memory_candidate = build_explicit_goal_candidate(
                user_id=principal.user_id,
                goal_id=payload.goal_id,
                request_id=payload.memory_declaration.request_id,
                title=payload.memory_declaration.title,
                target_outcome=payload.memory_declaration.target_outcome,
                deadline=payload.memory_declaration.deadline,
            )
    except ValidationError as exc:
        raise _invalid_memory_declaration() from exc
    try:
        return answer_tutor_question(
            session,
            user_id=principal.user_id,
            goal_id=payload.goal_id,
            thread_id=payload.thread_id,
            message=payload.message,
            memory_candidate=memory_candidate,
        )
    except MemoryIdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "memory.idempotency_conflict",
                "message": "The memory request identifier was already used with different content.",
            },
        ) from exc
    except MemoryGateInvariantError as exc:
        raise _invalid_memory_declaration() from exc
    except (LookupError, ConversationError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _invalid_memory_declaration() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "memory.invalid_declaration",
            "message": "The structured memory declaration is invalid.",
        },
    )
