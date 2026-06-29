from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.auth import get_current_user_id, validate_legacy_user_id
from backend.app.db import get_session
from backend.app.services.stage3 import complete_task, start_task


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskStartRequest(BaseModel):
    user_id: str | None = None


class TaskCompleteRequest(BaseModel):
    user_id: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
    evidence: dict = Field(default_factory=dict)


@router.post("/{task_id}/start")
def start_task_endpoint(
    task_id: str,
    payload: TaskStartRequest,
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    validate_legacy_user_id(payload.user_id, user_id)
    try:
        return start_task(session, user_id=user_id, task_id=task_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{task_id}/complete")
def complete_task_endpoint(
    task_id: str,
    payload: TaskCompleteRequest,
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    validate_legacy_user_id(payload.user_id, user_id)
    try:
        return complete_task(
            session,
            user_id=user_id,
            task_id=task_id,
            duration_minutes=payload.duration_minutes,
            evidence=payload.evidence,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
