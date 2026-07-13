from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.auth import validate_legacy_user_id
from backend.app.db import get_session
from backend.app.models import User
from backend.app.schemas import GoalCreateRequest, GoalCreateResponse
from backend.app.services.learning import DuplicateEmailError, create_goal


router = APIRouter(prefix="/api", tags=["goals"])


@router.post("/goals", response_model=GoalCreateResponse, status_code=201)
def create_goal_endpoint(
    payload: GoalCreateRequest,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    session: Session = Depends(get_session),
) -> GoalCreateResponse:
    current_user_id = x_user_id.strip() if x_user_id is not None else None
    requested_user_id = payload.user_id or current_user_id
    if current_user_id:
        validate_legacy_user_id(payload.user_id, current_user_id)
    if requested_user_id is not None and session.get(User, requested_user_id) is not None:
        if not current_user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X-User-Id header is required to create a goal for an existing user.",
            )

    try:
        result = create_goal(
            session,
            user_id=requested_user_id,
            email=payload.email,
            display_name=payload.display_name,
            title=payload.title,
            target_outcome=payload.target_outcome,
            deadline=payload.deadline,
            weekly_hours_target=payload.weekly_hours_target,
            learning_preferences=payload.learning_preferences,
            available_slots=payload.available_slots,
        )
    except DuplicateEmailError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return GoalCreateResponse(user_id=result.user_id, goal_id=result.id, status=result.status)
