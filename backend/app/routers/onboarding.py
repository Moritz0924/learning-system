from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.auth import get_current_user_id, validate_legacy_user_id
from backend.app.db import get_session
from backend.app.schemas import DiagnosisRequest, DiagnosisResponse
from backend.app.services.learning import NotFoundError, submit_onboarding_diagnosis


router = APIRouter(prefix="/api", tags=["onboarding"])


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
