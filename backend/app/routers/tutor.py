from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db import get_session
from backend.app.services.stage3 import answer_tutor_question


router = APIRouter(prefix="/api/tutor", tags=["tutor"])


class TutorChatRequest(BaseModel):
    user_id: str
    goal_id: str
    thread_id: str
    message: str


@router.post("/chat")
def tutor_chat_endpoint(payload: TutorChatRequest, session: Session = Depends(get_session)) -> dict:
    return answer_tutor_question(
        session,
        user_id=payload.user_id,
        goal_id=payload.goal_id,
        thread_id=payload.thread_id,
        message=payload.message,
    )
