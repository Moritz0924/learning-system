from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db import get_session
from backend.app.schemas import GoalCreateRequest, GoalCreateResponse
from backend.app.services.learning import create_goal


router = APIRouter(prefix="/api", tags=["goals"])


@router.post("/goals", response_model=GoalCreateResponse, status_code=201)
def create_goal_endpoint(
    payload: GoalCreateRequest,
    session: Session = Depends(get_session),
) -> GoalCreateResponse:
    result = create_goal(
        session,
        user_id=payload.user_id,
        email=payload.email,
        display_name=payload.display_name,
        title=payload.title,
        target_outcome=payload.target_outcome,
        deadline=payload.deadline,
        weekly_hours_target=payload.weekly_hours_target,
        learning_preferences=payload.learning_preferences,
        available_slots=payload.available_slots,
    )
    return GoalCreateResponse(user_id=result.user_id, goal_id=result.id, status=result.status)
