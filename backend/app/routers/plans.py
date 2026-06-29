from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.auth import get_current_user_id, validate_legacy_user_id
from backend.app.db import get_session
from backend.app.services.stage3 import PlanApplicationConflict, apply_plan_adjustment, request_replan


router = APIRouter(prefix="/api/plans", tags=["plans"])


class ReplanRequest(BaseModel):
    user_id: str | None = None
    goal_id: str
    thread_id: str
    message: str


class ApplyPlanAdjustmentRequest(BaseModel):
    user_id: str | None = None
    goal_id: str


@router.post("/replan")
def replan_endpoint(
    payload: ReplanRequest,
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    validate_legacy_user_id(payload.user_id, user_id)
    try:
        return request_replan(
            session,
            user_id=user_id,
            goal_id=payload.goal_id,
            message=payload.message,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/adjustments/{adjustment_id}/apply")
def apply_plan_adjustment_endpoint(
    adjustment_id: str,
    payload: ApplyPlanAdjustmentRequest,
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    validate_legacy_user_id(payload.user_id, user_id)
    try:
        return apply_plan_adjustment(
            session,
            adjustment_id=adjustment_id,
            user_id=user_id,
            goal_id=payload.goal_id,
        )
    except PlanApplicationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
