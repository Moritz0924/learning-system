from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.auth import get_current_user_id, validate_legacy_user_id
from backend.app.db import get_session
from backend.app.models import User
from backend.app.schemas import (
    DiagnosisRequest,
    DiagnosisResponse,
    GoalCreateResponse,
    OnboardingInitializeRequest,
    OnboardingInitializeResponse,
)
from backend.app.services.learning import (
    DiagnosisSubmissionResult,
    DuplicateEmailError,
    NotFoundError,
    initialize_onboarding,
    submit_onboarding_diagnosis,
)


router = APIRouter(prefix="/api", tags=["onboarding"])


@router.post("/onboarding/initialize", response_model=OnboardingInitializeResponse, status_code=201)
def initialize_onboarding_endpoint(
    payload: OnboardingInitializeRequest,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    session: Session = Depends(get_session),
) -> OnboardingInitializeResponse:
    current_user_id = x_user_id.strip() if x_user_id is not None else None
    requested_user_id = payload.user_id or current_user_id
    if current_user_id:
        validate_legacy_user_id(payload.user_id, current_user_id)
    if requested_user_id is not None and session.get(User, requested_user_id) is not None and not current_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header is required to initialize onboarding for an existing user.",
        )
    try:
        result = initialize_onboarding(
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
            self_assessment=payload.self_assessment,
            submitted_answers=payload.submitted_answers,
        )
    except DuplicateEmailError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return OnboardingInitializeResponse(
        goal=GoalCreateResponse(
            user_id=result.goal.user_id,
            goal_id=result.goal.id,
            status=result.goal.status,
        ),
        diagnosis=_diagnosis_response(result.diagnosis),
        state=result.state,
    )


@router.post("/onboarding/diagnosis", response_model=DiagnosisResponse, status_code=201)
def submit_diagnosis_endpoint(
    payload: DiagnosisRequest,
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> DiagnosisResponse:
    validate_legacy_user_id(payload.user_id, user_id)
    try:
        result = submit_onboarding_diagnosis(
            session,
            user_id=user_id,
            goal_id=payload.goal_id,
            self_assessment=payload.self_assessment,
            submitted_answers=payload.submitted_answers,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return _diagnosis_response(result)


def _diagnosis_response(result: DiagnosisSubmissionResult) -> DiagnosisResponse:
    return DiagnosisResponse(
        baseline_diagnostic_id=result.baseline_diagnostic_id,
        entry_node_id=result.entry_node_id,
        entry_node_code=result.entry_node_code,
        baseline_summary=result.baseline_summary,
        knowledge_gaps=result.knowledge_gaps,
        initial_mastery=result.initial_mastery,
        evidence_json=result.evidence_json,
        active_plan_id=result.active_plan_id,
        active_plan_version=result.active_plan_version,
    )
