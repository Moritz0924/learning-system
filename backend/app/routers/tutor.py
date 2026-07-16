from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.application.tutor_service import answer_tutor_question


router = APIRouter(prefix="/api/tutor", tags=["tutor"])


class TutorChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_id: str
    thread_id: str
    message: str


@router.post("/chat")
def tutor_chat_endpoint(
    payload: TutorChatRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return answer_tutor_question(
            session,
            user_id=principal.user_id,
            goal_id=payload.goal_id,
            thread_id=payload.thread_id,
            message=payload.message,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
