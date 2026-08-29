from pydantic import BaseModel, ConfigDict, Field, field_validator
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.application.learning_service import complete_task, start_task
from backend.app.core.exceptions import TaskStateConflict


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration_minutes: int | None = Field(default=None, ge=1)
    evidence: dict = Field(default_factory=dict)

    @field_validator("evidence")
    @classmethod
    def evidence_cannot_set_server_measurements(cls, evidence: dict) -> dict:
        reserved = {"measurement_source", "duration_seconds", "duration_minutes", "started_at", "ended_at"}
        conflict = reserved.intersection(evidence)
        if conflict:
            raise ValueError(f"evidence contains server-owned fields: {', '.join(sorted(conflict))}")
        return evidence


@router.post("/{task_id}/start")
def start_task_endpoint(
    task_id: str,
    payload: TaskStartRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return start_task(session, user_id=principal.user_id, task_id=task_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaskStateConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/{task_id}/complete")
def complete_task_endpoint(
    task_id: str,
    payload: TaskCompleteRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return complete_task(
            session,
            user_id=principal.user_id,
            task_id=task_id,
            duration_minutes=payload.duration_minutes,
            evidence=payload.evidence,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaskStateConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": exc.code, "message": str(exc)}) from exc
