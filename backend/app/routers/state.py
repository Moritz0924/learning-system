from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.auth import get_current_user_id
from backend.app.db import get_session
from backend.app.schemas import StateResponse, TodayTasksResponse
from backend.app.services.learning import NotFoundError, get_current_state, get_today_tasks


router = APIRouter(prefix="/api", tags=["state"])


@router.get("/state/current", response_model=StateResponse)
def get_current_state_endpoint(
    goal_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return get_current_state(session, user_id=user_id, goal_id=goal_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/tasks/today", response_model=TodayTasksResponse)
def get_today_tasks_endpoint(
    goal_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return get_today_tasks(session, user_id=user_id, goal_id=goal_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
