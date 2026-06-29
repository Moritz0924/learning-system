from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.auth import get_current_user_id, validate_legacy_user_id
from backend.app.db import get_session
from backend.app.services.stage3 import answer_tutor_question


router = APIRouter(prefix="/api/tutor", tags=["tutor"])


class TutorChatRequest(BaseModel):
    user_id: str | None = None
    goal_id: str
    thread_id: str
    message: str


@router.post("/chat")
def tutor_chat_endpoint(
    payload: TutorChatRequest,
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    validate_legacy_user_id(payload.user_id, user_id)
    try:
        return answer_tutor_question(
            session,
            user_id=user_id,
            goal_id=payload.goal_id,
            thread_id=payload.thread_id,
            message=payload.message,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
