from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.application.planning_service import apply_plan_adjustment, request_replan
from backend.app.core.exceptions import PlanApplicationConflict


router = APIRouter(prefix="/api/plans", tags=["plans"])


class ReplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_id: str
    thread_id: str
    message: str


class ApplyPlanAdjustmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_id: str


@router.post("/replan")
def replan_endpoint(
    payload: ReplanRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return request_replan(
            session,
            user_id=principal.user_id,
            goal_id=payload.goal_id,
            message=payload.message,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/adjustments/{adjustment_id}/apply")
def apply_plan_adjustment_endpoint(
    adjustment_id: str,
    payload: ApplyPlanAdjustmentRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return apply_plan_adjustment(
            session,
            adjustment_id=adjustment_id,
            user_id=principal.user_id,
            goal_id=payload.goal_id,
        )
    except PlanApplicationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
