from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.schemas import GoalCreateRequest, GoalCreateResponse, GoalListResponse
from backend.app.services.learning import NotFoundError, create_goal, list_goals


router = APIRouter(prefix="/api", tags=["goals"])


@router.get("/goals", response_model=GoalListResponse)
def list_goals_endpoint(
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> GoalListResponse:
    return GoalListResponse(goals=list_goals(session, user_id=principal.user_id))


@router.post("/goals", response_model=GoalCreateResponse, status_code=201)
def create_goal_endpoint(
    payload: GoalCreateRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> GoalCreateResponse:
    try:
        result = create_goal(
            session,
            user_id=principal.user_id,
            title=payload.title,
            target_outcome=payload.target_outcome,
            deadline=payload.deadline,
            weekly_hours_target=payload.weekly_hours_target,
            learning_preferences=payload.learning_preferences,
            available_slots=payload.available_slots,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return GoalCreateResponse(user_id=result.user_id, goal_id=result.id, status=result.status)
