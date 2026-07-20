from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.api.schemas.assessments import (
    AssessmentPublicResponse,
    PhaseAssessmentPublicResponse,
)
from backend.app.application.assessment_workflow_service import AssessmentWorkflowService
from backend.app.domain.assessment.errors import AssessmentDomainError


def create_assessment(
    session: Session,
    *,
    user_id: str,
    request_id: str,
    goal_id: str,
    thread_id: str,
    assessment_type: str,
    knowledge_node_ids: list[str],
) -> AssessmentPublicResponse:
    return AssessmentWorkflowService(session).create(
        user_id=user_id,
        request_id=request_id,
        goal_id=goal_id,
        thread_id=thread_id,
        assessment_type=assessment_type,
        knowledge_node_ids=knowledge_node_ids,
    )

def create_phase_assessment(
    session: Session,
    *,
    user_id: str,
    request_id: str,
    goal_id: str,
    thread_id: str,
    phase_code: str,
    knowledge_node_ids: list[str],
) -> PhaseAssessmentPublicResponse:
    result = AssessmentWorkflowService(session).create(
        user_id=user_id,
        request_id=request_id,
        goal_id=goal_id,
        thread_id=thread_id,
        assessment_type="phase",
        knowledge_node_ids=knowledge_node_ids,
        phase_code=phase_code,
    )
    if not isinstance(result, PhaseAssessmentPublicResponse):
        raise RuntimeError("phase assessment workflow did not create phase state")
    return result

def submit_assessment(
    session: Session,
    *,
    assessment_id: str,
    user_id: str,
    request_id: str,
    answers: dict[str, str],
) -> dict:
    return AssessmentWorkflowService(session).submit(
        assessment_id=assessment_id,
        user_id=user_id,
        request_id=request_id,
        answers=answers,
    ).model_dump()
