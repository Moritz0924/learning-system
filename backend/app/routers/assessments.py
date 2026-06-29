from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.auth import get_current_user_id, validate_legacy_user_id
from backend.app.db import get_session
from backend.app.services.stage3 import create_assessment, create_phase_assessment, submit_assessment


router = APIRouter(prefix="/api/assessments", tags=["assessments"])


class AssessmentCreateRequest(BaseModel):
    user_id: str | None = None
    goal_id: str
    thread_id: str
    assessment_type: str = "daily"
    knowledge_node_ids: list[str] = Field(default_factory=list)


class AssessmentSubmitRequest(BaseModel):
    user_id: str | None = None
    answers: dict[str, str]


class PhaseAssessmentCreateRequest(BaseModel):
    user_id: str | None = None
    goal_id: str
    thread_id: str
    phase_code: str
    knowledge_node_ids: list[str] = Field(default_factory=list)


@router.post("", status_code=201)
def create_assessment_endpoint(
    payload: AssessmentCreateRequest,
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    validate_legacy_user_id(payload.user_id, user_id)
    try:
        return create_assessment(
            session,
            user_id=user_id,
            goal_id=payload.goal_id,
            assessment_type=payload.assessment_type,
            knowledge_node_ids=payload.knowledge_node_ids,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/phase", status_code=201)
def create_phase_assessment_endpoint(
    payload: PhaseAssessmentCreateRequest,
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    validate_legacy_user_id(payload.user_id, user_id)
    try:
        return create_phase_assessment(
            session,
            user_id=user_id,
            goal_id=payload.goal_id,
            thread_id=payload.thread_id,
            phase_code=payload.phase_code,
            knowledge_node_ids=payload.knowledge_node_ids,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{assessment_id}/submit")
def submit_assessment_endpoint(
    assessment_id: str,
    payload: AssessmentSubmitRequest,
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    validate_legacy_user_id(payload.user_id, user_id)
    try:
        return submit_assessment(
            session,
            assessment_id=assessment_id,
            user_id=user_id,
            answers=payload.answers,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
