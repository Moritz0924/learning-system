from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.schemas.onboarding import (
    DiagnosticTemplateResponse,
    DynamicDiagnosticDraftRequest,
    DynamicDiagnosticDraftResponse,
    DynamicReassessDraftRequest,
    InitializeFromDraftRequest,
    OnboardingInitializeRequest,
    OnboardingInitializeResponse,
    ReassessFromDraftRequest,
)
from backend.app.application.onboarding_service import (
    DEFAULT_DIAGNOSTIC_TEMPLATE_REPOSITORY,
    DynamicOnboardingError,
    OnboardingService,
)
from backend.app.api.deps import get_current_principal
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.infrastructure.secrets import SecretStore
from backend.app.routers.config import get_secret_store
from backend.app.domain.diagnosis.contracts import public_template
from backend.app.domain.diagnosis.validation import DiagnosisValidationError
from backend.app.schemas import (
    DiagnosisRequest,
    DiagnosisResponse,
    GoalCreateResponse,
    OnboardingInitializeRequest as LegacyOnboardingInitializeRequest,
)
from backend.app.services.learning import (
    DiagnosisSubmissionResult,
    NotFoundError,
    initialize_onboarding,
    submit_onboarding_diagnosis,
)


router = APIRouter(prefix="/api", tags=["onboarding"])


@router.post(
    "/onboarding/dynamic-drafts",
    response_model=DynamicDiagnosticDraftResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_dynamic_draft_endpoint(
    payload: DynamicDiagnosticDraftRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    secret_store: SecretStore | None = Depends(get_secret_store),
) -> dict:
    try:
        return OnboardingService(session, secret_store=secret_store).create_dynamic_draft(
            user_id=principal.user_id,
            request=payload,
        )
    except DynamicOnboardingError as exc:
        raise _dynamic_http_error(exc) from exc


@router.post(
    "/onboarding/reassess-drafts",
    response_model=DynamicDiagnosticDraftResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_reassess_draft_endpoint(
    payload: DynamicReassessDraftRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    secret_store: SecretStore | None = Depends(get_secret_store),
) -> dict:
    try:
        return OnboardingService(session, secret_store=secret_store).create_reassess_draft(
            user_id=principal.user_id,
            request=payload,
        )
    except DynamicOnboardingError as exc:
        raise _dynamic_http_error(exc) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/onboarding/initialize-from-draft",
    response_model=OnboardingInitializeResponse,
    status_code=status.HTTP_201_CREATED,
)
def initialize_from_draft_endpoint(
    payload: InitializeFromDraftRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    secret_store: SecretStore | None = Depends(get_secret_store),
) -> OnboardingInitializeResponse:
    try:
        result = OnboardingService(session, secret_store=secret_store).initialize_from_draft(
            user_id=principal.user_id,
            request=payload,
        )
    except DynamicOnboardingError as exc:
        raise _dynamic_http_error(exc) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return OnboardingInitializeResponse(
        goal=GoalCreateResponse(
            user_id=result.goal.user_id,
            goal_id=result.goal.id,
            status=result.goal.status,
        ),
        diagnosis=_diagnosis_response(result.diagnosis),
        state=result.state,
        replayed=result.replayed,
    )


@router.post("/onboarding/reassess-from-draft", response_model=OnboardingInitializeResponse)
def reassess_from_draft_endpoint(
    payload: ReassessFromDraftRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    secret_store: SecretStore | None = Depends(get_secret_store),
) -> OnboardingInitializeResponse:
    try:
        result = OnboardingService(session, secret_store=secret_store).reassess_from_draft(
            user_id=principal.user_id,
            request=payload,
        )
    except DynamicOnboardingError as exc:
        raise _dynamic_http_error(exc) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return OnboardingInitializeResponse(
        goal=GoalCreateResponse(
            user_id=result.goal.user_id,
            goal_id=result.goal.id,
            status=result.goal.status,
        ),
        diagnosis=_diagnosis_response(result.diagnosis),
        state=result.state,
        replayed=result.replayed,
    )


@router.get(
    "/onboarding/diagnostic-template",
    response_model=DiagnosticTemplateResponse,
)
def get_diagnostic_template_endpoint(
    domain: str = Query(..., min_length=1, max_length=64),
    principal: Principal = Depends(get_current_principal),
) -> DiagnosticTemplateResponse:
    del principal
    try:
        loaded = DEFAULT_DIAGNOSTIC_TEMPLATE_REPOSITORY.load(domain=domain)
    except DiagnosisValidationError as exc:
        raise _diagnosis_http_error(exc) from exc
    return DiagnosticTemplateResponse.model_validate(
        public_template(loaded.template).model_dump(mode="json")
    )


@router.post("/onboarding/initialize", response_model=OnboardingInitializeResponse, status_code=201)
def initialize_onboarding_endpoint(
    payload: OnboardingInitializeRequest | LegacyOnboardingInitializeRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> OnboardingInitializeResponse:
    try:
        if isinstance(payload, OnboardingInitializeRequest):
            result = OnboardingService(session).initialize(
                user_id=principal.user_id,
                request=payload,
            )
        else:
            result = initialize_onboarding(
                session,
                user_id=principal.user_id,
                title=payload.title,
                target_outcome=payload.target_outcome,
                deadline=payload.deadline,
                weekly_hours_target=payload.weekly_hours_target,
                learning_preferences=payload.learning_preferences,
                available_slots=payload.available_slots,
                self_assessment=payload.self_assessment,
                submitted_answers=payload.submitted_answers,
            )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DiagnosisValidationError as exc:
        raise _diagnosis_http_error(exc) from exc
    return OnboardingInitializeResponse(
        goal=GoalCreateResponse(
            user_id=result.goal.user_id,
            goal_id=result.goal.id,
            status=result.goal.status,
        ),
        diagnosis=_diagnosis_response(result.diagnosis),
        state=result.state,
        replayed=getattr(result, "replayed", False),
    )


@router.post("/onboarding/diagnosis", response_model=DiagnosisResponse, status_code=201)
def submit_diagnosis_endpoint(
    payload: DiagnosisRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> DiagnosisResponse:
    try:
        result = submit_onboarding_diagnosis(
            session,
            user_id=principal.user_id,
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
        template_version=getattr(result, "template_version", "legacy_unversioned"),
        template_hash=getattr(result, "template_hash", None),
        score_breakdown=getattr(result, "score_breakdown", {}),
    )


def _diagnosis_http_error(exc: DiagnosisValidationError) -> HTTPException:
    if exc.code == "template_not_found":
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "diagnosis.template_not_found",
                "message": "The requested diagnostic template was not found.",
            },
        )
    if exc.code in {"invalid_template", "template_domain_mismatch"}:
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "diagnosis.template_invalid",
                "message": "The diagnostic template is unavailable.",
            },
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": f"diagnosis.{exc.code}", "message": str(exc)},
    )


def _dynamic_http_error(exc: DynamicOnboardingError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )
