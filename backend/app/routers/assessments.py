from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.api.schemas.assessments import (
    AssessmentCreateRequest,
    AssessmentPublicResponse,
    AssessmentSubmitRequest,
    PhaseAssessmentCreateRequest,
    PhaseAssessmentPublicResponse,
)
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.application.assessment_service import create_assessment, create_phase_assessment, submit_assessment
from backend.app.core.exceptions import (
    AssessmentAnswerValidationError,
    AssessmentSubmissionConflict,
)


router = APIRouter(prefix="/api/assessments", tags=["assessments"])


@router.post("", status_code=201, response_model=AssessmentPublicResponse)
def create_assessment_endpoint(
    payload: AssessmentCreateRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return create_assessment(
            session,
            user_id=principal.user_id,
            goal_id=payload.goal_id,
            thread_id=payload.thread_id,
            assessment_type=payload.assessment_type,
            knowledge_node_ids=payload.knowledge_node_ids,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/phase", status_code=201, response_model=PhaseAssessmentPublicResponse)
def create_phase_assessment_endpoint(
    payload: PhaseAssessmentCreateRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return create_phase_assessment(
            session,
            user_id=principal.user_id,
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
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return submit_assessment(
            session,
            assessment_id=assessment_id,
            user_id=principal.user_id,
            answers=payload.answers,
            submission_id=str(payload.request_id),
        )
    except AssessmentSubmissionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AssessmentAnswerValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
