from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import logging
import re
import unicodedata
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.api.schemas.onboarding import (
    DynamicDiagnosticDraftRequest,
    DynamicReassessDraftRequest,
    GoalInitializationInput,
    InitializeFromDraftRequest,
    OnboardingInitializeRequest,
    ReassessFromDraftRequest,
)
from backend.app.application.config_service import RuntimeResolutionError, RuntimeResolver
from backend.app.application.memory_candidate_service import generated_memory_idempotency_key
from backend.app.application.memory_privacy_service import MemoryPrivacyService
from backend.app.application.memory_write_service import MemoryWriteService
from backend.app.application.task_localization import task_copy
from backend.app.domain.assessment.contracts import ObserverSignalBundleV2
from backend.app.domain.assessment.observer_policy import decide_observer
from backend.app.domain.assessment.plan_policy import build_plan_proposal
from backend.app.domain.diagnosis.contracts import CurriculumNodeDefinition
from backend.app.domain.diagnosis.scoring import score_diagnosis
from backend.app.domain.diagnosis.validation import validate_diagnostic_answers
from backend.app.infrastructure.diagnosis.template_repository import (
    DiagnosticTemplateRepository,
    LoadedDiagnosticTemplate,
)
from backend.app.models import (
    BaselineDiagnostic,
    Curriculum,
    KnowledgeEdge,
    KnowledgeNode,
    LearnerProfile,
    LearningGoal,
    LearningPlan,
    LearningSession,
    LearningStateSnapshot,
    MasteryRecord,
    PlanAdjustmentRecord,
    PlanTask,
    User,
    UserDiagnosticDraft,
)
from backend.app.infrastructure.secrets import SecretStore
from backend.app.domain.memory import (
    CreateMemoryCommand,
    MemoryCandidate,
    evaluate_memory_candidates,
)
from backend.app.services.curriculum import ensure_curriculum_seeded, ordered_nodes
from backend.app.services.learning import NotFoundError, get_current_state
from backend.app.services.llm_gateway import EvaluationProviderError


DEFAULT_DIAGNOSTIC_TEMPLATE_REPOSITORY = DiagnosticTemplateRepository()
_DYNAMIC_DRAFT_TTL = timedelta(hours=1)
logger = logging.getLogger(__name__)


class DynamicOnboardingError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class _RoadmapFeasibilityError(ValueError):
    def __init__(self, reason_code: str, constraints: dict[str, object]) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.constraints = constraints


class _GeneratedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class _GeneratedOption(_GeneratedModel):
    option_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=300)


class _GeneratedQuestion(_GeneratedModel):
    question_id: str = Field(min_length=1, max_length=64)
    skill_id: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=1000)
    options: list[_GeneratedOption] = Field(min_length=2, max_length=6)
    correct_option_id: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_options(self) -> "_GeneratedQuestion":
        option_ids = [option.option_id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("option ids must be unique")
        option_labels = [
            " ".join(unicodedata.normalize("NFKC", option.label).casefold().split())
            for option in self.options
        ]
        if len(option_labels) != len(set(option_labels)):
            raise ValueError("option labels must be unique")
        if self.correct_option_id not in option_ids:
            raise ValueError("correct option must exist")
        return self


class _GeneratedDiagnostic(_GeneratedModel):
    title: str = Field(min_length=1, max_length=200)
    questions: list[_GeneratedQuestion] = Field(min_length=3, max_length=5)

    @model_validator(mode="after")
    def validate_question_ids(self) -> "_GeneratedDiagnostic":
        question_ids = [question.question_id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question ids must be unique")
        return self


class _GeneratedNode(_GeneratedModel):
    node_id: str = Field(min_length=1, max_length=64)
    skill_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=1000)
    order: int = Field(ge=1, le=6)
    estimated_minutes: int = Field(ge=15, le=240)
    due_day: int = Field(ge=1, le=365)


class _GeneratedStage(_GeneratedModel):
    stage_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=1000)
    order: int = Field(ge=1, le=8)
    nodes: list[_GeneratedNode] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_node_order(self) -> "_GeneratedStage":
        node_ids = [node.node_id for node in self.nodes]
        orders = [node.order for node in self.nodes]
        if len(node_ids) != len(set(node_ids)) or sorted(orders) != list(range(1, len(orders) + 1)):
            raise ValueError("node ids must be unique and orders contiguous")
        return self


class _GeneratedRoadmap(_GeneratedModel):
    title: str = Field(min_length=1, max_length=200)
    stages: list[_GeneratedStage] = Field(min_length=3, max_length=8)

    @model_validator(mode="after")
    def validate_stage_order(self) -> "_GeneratedRoadmap":
        stage_ids = [stage.stage_id for stage in self.stages]
        orders = [stage.order for stage in self.stages]
        node_ids = [node.node_id for stage in self.stages for node in stage.nodes]
        if len(stage_ids) != len(set(stage_ids)) or sorted(orders) != list(range(1, len(orders) + 1)):
            raise ValueError("stage ids must be unique and orders contiguous")
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node ids must be globally unique")
        return self


@dataclass(frozen=True)
class OnboardingDiagnosisResult:
    baseline_diagnostic_id: str
    entry_node_id: str
    entry_node_code: str
    baseline_summary: str
    knowledge_gaps: list[dict]
    initial_mastery: dict
    evidence_json: dict
    active_plan_id: str
    active_plan_version: int
    template_version: str
    template_hash: str | None
    score_breakdown: dict


@dataclass(frozen=True)
class AtomicOnboardingInitializationResult:
    goal: LearningGoal
    diagnosis: OnboardingDiagnosisResult
    state: dict
    replayed: bool = False


class OnboardingService:
    def __init__(
        self,
        session: Session,
        template_repository: DiagnosticTemplateRepository | None = None,
        secret_store: SecretStore | None = None,
    ) -> None:
        self._session = session
        self._secret_store = secret_store
        self._template_repository = (
            template_repository or DEFAULT_DIAGNOSTIC_TEMPLATE_REPOSITORY
        )

    def create_dynamic_draft(
        self, *, user_id: str, request: DynamicDiagnosticDraftRequest
    ) -> dict:
        request_id = str(request.request_id)
        existing = self._session.scalar(
            select(UserDiagnosticDraft).where(
                UserDiagnosticDraft.user_id == user_id,
                UserDiagnosticDraft.request_id == request_id,
            )
        )
        if existing is not None:
            return self._draft_payload(existing)
        if self._session.get(User, user_id) is None:
            raise NotFoundError(f"user {user_id} not found")
        self._validate_goal_deadline(request.goal)
        generated = self._generate_diagnostic(user_id=user_id, request=request)
        draft = UserDiagnosticDraft(
            id=f"draft-{uuid4()}",
            user_id=user_id,
            request_id=request_id,
            locale=request.locale,
            goal_input=request.goal.model_dump(mode="json"),
            title=generated.title,
            public_questions=[
                {
                    "question_id": question.question_id,
                    "prompt": question.prompt,
                    "options": [option.model_dump(mode="json") for option in question.options],
                }
                for question in generated.questions
            ],
            scoring_key={
                question.question_id: {
                    "correct_option_id": question.correct_option_id,
                    "skill_id": question.skill_id,
                }
                for question in generated.questions
            },
            expires_at=datetime.utcnow() + _DYNAMIC_DRAFT_TTL,
        )
        self._session.add(draft)
        try:
            self._session.commit()
            return self._draft_payload(draft)
        except IntegrityError:
            self._session.rollback()
            existing = self._session.scalar(
                select(UserDiagnosticDraft).where(
                    UserDiagnosticDraft.user_id == user_id,
                    UserDiagnosticDraft.request_id == request_id,
                )
            )
            if existing is None:
                raise
            return self._draft_payload(existing)

    def create_reassess_draft(
        self, *, user_id: str, request: DynamicReassessDraftRequest
    ) -> dict:
        request_id = str(request.request_id)
        goal = self._load_reassess_goal(user_id=user_id, goal_id=request.goal_id)
        goal_input = self._goal_input_from_goal(goal)
        existing = self._session.scalar(
            select(UserDiagnosticDraft).where(
                UserDiagnosticDraft.user_id == user_id,
                UserDiagnosticDraft.request_id == request_id,
            )
        )
        if existing is not None:
            self._validate_reassess_draft_goal(
                draft=existing,
                goal=goal,
                locale=request.locale,
            )
            return self._draft_payload(existing)
        self._validate_goal_deadline(goal_input)
        generated = self._generate_diagnostic(
            user_id=user_id,
            request=DynamicDiagnosticDraftRequest(
                request_id=request.request_id,
                locale=request.locale,
                goal=goal_input,
            ),
        )
        draft = UserDiagnosticDraft(
            id=f"draft-{uuid4()}",
            user_id=user_id,
            request_id=request_id,
            locale=request.locale,
            goal_input={
                **goal_input.model_dump(mode="json"),
                "_reassess_goal_id": goal.id,
            },
            title=generated.title,
            public_questions=[
                {
                    "question_id": question.question_id,
                    "prompt": question.prompt,
                    "options": [option.model_dump(mode="json") for option in question.options],
                }
                for question in generated.questions
            ],
            scoring_key={
                question.question_id: {
                    "correct_option_id": question.correct_option_id,
                    "skill_id": question.skill_id,
                }
                for question in generated.questions
            },
            expires_at=datetime.utcnow() + _DYNAMIC_DRAFT_TTL,
        )
        self._session.add(draft)
        try:
            self._session.commit()
            return self._draft_payload(draft)
        except IntegrityError:
            self._session.rollback()
            existing = self._session.scalar(
                select(UserDiagnosticDraft).where(
                    UserDiagnosticDraft.user_id == user_id,
                    UserDiagnosticDraft.request_id == request_id,
                )
            )
            if existing is None:
                raise
            self._validate_reassess_draft_goal(
                draft=existing,
                goal=goal,
                locale=request.locale,
            )
            return self._draft_payload(existing)

    def initialize_from_draft(
        self, *, user_id: str, request: InitializeFromDraftRequest
    ) -> AtomicOnboardingInitializationResult:
        request_id = str(request.request_id)
        try:
            existing = self._find_existing_diagnostic(user_id=user_id, request_id=request_id)
            if existing is not None:
                self._validate_dynamic_replay(
                    diagnostic=existing,
                    user_id=user_id,
                    request=request,
                )
                return self._result_from_diagnostic(existing, replayed=True)

            draft = self._session.scalar(
                select(UserDiagnosticDraft).where(
                    UserDiagnosticDraft.id == request.draft_id,
                    UserDiagnosticDraft.user_id == user_id,
                )
            )
            if draft is None:
                raise DynamicOnboardingError(
                    "onboarding.draft_not_found", "Diagnostic draft not found.", 404
                )
            if draft.consumed_at is not None:
                raise DynamicOnboardingError(
                    "onboarding.draft_consumed", "Diagnostic draft has already been used.", 409
                )
            if datetime.utcnow() >= draft.expires_at:
                raise DynamicOnboardingError(
                    "onboarding.draft_expired", "Diagnostic draft has expired.", 410
                )
            answers = self._validate_draft_answers(draft, request)
            goal_input = GoalInitializationInput.model_validate(draft.goal_input)
            self._validate_goal_deadline(goal_input)
            roadmap = self._generate_roadmap(
                user_id=user_id,
                request_id=request_id,
                draft=draft,
                goal=goal_input,
                answers=answers,
            )
            result = self._create_dynamic_workspace(
                user_id=user_id,
                request_id=request_id,
                draft=draft,
                goal_input=goal_input,
                answers=answers,
                roadmap=roadmap,
            )
            claimed = self._session.execute(
                update(UserDiagnosticDraft)
                .where(
                    UserDiagnosticDraft.id == draft.id,
                    UserDiagnosticDraft.user_id == user_id,
                    UserDiagnosticDraft.consumed_at.is_(None),
                )
                .values(consumed_at=datetime.utcnow())
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                raise DynamicOnboardingError(
                    "onboarding.draft_consumed",
                    "Diagnostic draft has already been used.",
                    409,
                )
            self._session.flush()
            self._session.commit()
            return result
        except IntegrityError:
            self._session.rollback()
            existing = self._find_existing_diagnostic(user_id=user_id, request_id=request_id)
            if existing is None:
                raise
            self._validate_dynamic_replay(
                diagnostic=existing,
                user_id=user_id,
                request=request,
            )
            return self._result_from_diagnostic(existing, replayed=True)
        except Exception:
            self._session.rollback()
            raise

    def reassess_from_draft(
        self, *, user_id: str, request: ReassessFromDraftRequest
    ) -> AtomicOnboardingInitializationResult:
        request_id = str(request.request_id)
        try:
            existing = self._find_existing_diagnostic(user_id=user_id, request_id=request_id)
            if existing is not None:
                self._validate_reassess_replay(
                    diagnostic=existing,
                    user_id=user_id,
                    request=request,
                )
                return self._result_from_diagnostic(existing, replayed=True)

            goal = self._load_reassess_goal(
                user_id=user_id,
                goal_id=request.goal_id,
                for_update=True,
            )
            existing = self._find_existing_diagnostic(user_id=user_id, request_id=request_id)
            if existing is not None:
                self._validate_reassess_replay(
                    diagnostic=existing,
                    user_id=user_id,
                    request=request,
                )
                result = self._result_from_diagnostic(existing, replayed=True)
                self._session.commit()
                return result
            snapshot = self._session.scalar(
                select(LearningStateSnapshot)
                .where(
                    LearningStateSnapshot.user_id == user_id,
                    LearningStateSnapshot.goal_id == goal.id,
                )
                .with_for_update()
            )
            if snapshot is None:
                raise DynamicOnboardingError(
                    "onboarding.reassess_state_missing",
                    "The current learning state is unavailable.",
                    409,
                )
            active_plan = self._session.scalar(
                select(LearningPlan)
                .where(
                    LearningPlan.id == snapshot.active_plan_id,
                    LearningPlan.user_id == user_id,
                    LearningPlan.goal_id == goal.id,
                    LearningPlan.status == "active",
                )
                .with_for_update()
            )
            if active_plan is None:
                raise DynamicOnboardingError(
                    "onboarding.reassess_state_missing",
                    "The current learning plan is unavailable.",
                    409,
                )
            if self._session.scalar(
                select(LearningSession.id).where(
                    LearningSession.user_id == user_id,
                    LearningSession.goal_id == goal.id,
                    LearningSession.status == "active",
                )
            ) is not None:
                raise DynamicOnboardingError(
                    "onboarding.active_learning_session",
                    "Finish or stop the active learning task before reassessing.",
                    409,
                )
            draft = self._session.scalar(
                select(UserDiagnosticDraft).where(
                    UserDiagnosticDraft.id == request.draft_id,
                    UserDiagnosticDraft.user_id == user_id,
                )
            )
            if draft is None:
                raise DynamicOnboardingError(
                    "onboarding.draft_not_found", "Diagnostic draft not found.", 404
                )
            self._validate_reassess_draft_goal(draft=draft, goal=goal, locale=draft.locale)
            if draft.consumed_at is not None:
                raise DynamicOnboardingError(
                    "onboarding.draft_consumed", "Diagnostic draft has already been used.", 409
                )
            if datetime.utcnow() >= draft.expires_at:
                raise DynamicOnboardingError(
                    "onboarding.draft_expired", "Diagnostic draft has expired.", 410
                )
            answers = self._validate_draft_answers(draft, request)
            goal_input = self._goal_input_from_goal(goal)
            self._validate_goal_deadline(goal_input)
            roadmap = self._generate_roadmap(
                user_id=user_id,
                request_id=request_id,
                draft=draft,
                goal=goal_input,
                answers=answers,
            )
            if self._session.scalar(
                select(LearningSession.id).where(
                    LearningSession.user_id == user_id,
                    LearningSession.goal_id == goal.id,
                    LearningSession.status == "active",
                )
            ) is not None:
                raise DynamicOnboardingError(
                    "onboarding.active_learning_session",
                    "Finish or stop the active learning task before reassessing.",
                    409,
                )
            active_plan.status = "replaced"
            self._session.flush()
            result = self._create_dynamic_workspace(
                user_id=user_id,
                request_id=request_id,
                draft=draft,
                goal_input=goal_input,
                answers=answers,
                roadmap=roadmap,
                existing_goal=goal,
                existing_snapshot=snapshot,
                plan_version=active_plan.version + 1,
            )
            claimed = self._session.execute(
                update(UserDiagnosticDraft)
                .where(
                    UserDiagnosticDraft.id == draft.id,
                    UserDiagnosticDraft.user_id == user_id,
                    UserDiagnosticDraft.consumed_at.is_(None),
                )
                .values(consumed_at=datetime.utcnow())
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                raise DynamicOnboardingError(
                    "onboarding.draft_consumed",
                    "Diagnostic draft has already been used.",
                    409,
                )
            self._session.flush()
            self._session.commit()
            return result
        except IntegrityError:
            self._session.rollback()
            existing = self._find_existing_diagnostic(user_id=user_id, request_id=request_id)
            if existing is None:
                raise
            self._validate_reassess_replay(
                diagnostic=existing,
                user_id=user_id,
                request=request,
            )
            return self._result_from_diagnostic(existing, replayed=True)
        except Exception:
            self._session.rollback()
            raise

    def _runtime_client(self, *, user_id: str, request_id: str, operation: str):
        try:
            return RuntimeResolver(
                self._session,
                user_id=user_id,
                secret_store=self._secret_store,
            ).resolve("reasoning")
        except RuntimeResolutionError as exc:
            self._log_model_failure(
                request_id=request_id,
                operation=operation,
                error_code=exc.code,
                validation_stage="runtime_resolution",
            )
            raise DynamicOnboardingError(
                "onboarding.dynamic_configuration_invalid",
                "Reasoning model configuration is unavailable.",
                503,
            ) from None
        except Exception:
            self._log_model_failure(
                request_id=request_id,
                operation=operation,
                error_code="runtime.resolution_failed",
                validation_stage="runtime_resolution",
            )
            raise DynamicOnboardingError(
                "onboarding.dynamic_provider_unavailable",
                "The model provider is temporarily unavailable.",
                503,
            ) from None

    def _generate_diagnostic(
        self, *, user_id: str, request: DynamicDiagnosticDraftRequest
    ) -> _GeneratedDiagnostic:
        prompt = (
            f"Return strict JSON only. Write all learner-visible text in locale {request.locale}. "
            "Create 3-5 single-choice diagnostic questions for this learning goal. "
            "question_id values must be unique. Questions may share the same skill_id; "
            "when they do, test that skill through different scenarios, perspectives, "
            "applications, or common misconceptions. Within each question, option_id values "
            "must be unique and correct_option_id must reference an existing option_id. "
            "Schema: {title, questions:[{question_id, skill_id, prompt, "
            "options:[{option_id,label}], correct_option_id}]}. "
            f"Goal: {json.dumps(request.goal.model_dump(mode='json'), ensure_ascii=False)}"
        )
        return self._complete_model(
            user_id=user_id,
            request_id=str(request.request_id),
            operation="diagnostic",
            prompt=prompt,
            response_type=_GeneratedDiagnostic,
        )

    def _generate_roadmap(
        self,
        *,
        user_id: str,
        request_id: str,
        draft: UserDiagnosticDraft,
        goal: GoalInitializationInput,
        answers: dict[str, str],
    ) -> _GeneratedRoadmap:
        skill_results = self._diagnostic_skill_results(draft, answers)
        allowed_skill_ids = set(skill_results)
        constraints = self._roadmap_constraints(goal, allowed_skill_ids)
        scored_answers = [
            {
                "question_id": question_id,
                "skill_id": key["skill_id"],
                "correct": answers[question_id] == key["correct_option_id"],
            }
            for question_id, key in draft.scoring_key.items()
        ]
        prompt = (
            f"Return strict JSON only. Write all learner-visible text in locale {draft.locale}. "
            "Create 3-8 ordered learning stages with 1-6 ordered task nodes per stage. "
            "Use globally unique stable string ids. Every diagnostic skill must be covered by at "
            "least one node, and every node skill_id must be one of the required diagnostic skill "
            "IDs. due_day values must be nondecreasing in roadmap order and within the exact range. "
            "Schema: {title, stages:[{stage_id,title,objective,order,nodes:["
            "{node_id,skill_id,title,objective,order,estimated_minutes,due_day}]}]}. "
            f"Goal: {json.dumps(goal.model_dump(mode='json'), ensure_ascii=False)}. "
            f"Required diagnostic skill IDs: {json.dumps(sorted(allowed_skill_ids))}. "
            "Grouped diagnostic skill results: "
            f"{json.dumps([skill_results[key] for key in sorted(skill_results)], separators=(',', ':'))}. "
            "Exact roadmap constraints: "
            f"today={constraints['today']}, deadline={constraints['deadline']}, "
            f"weekly_hours={constraints['weekly_hours']}, "
            f"weekly_minutes={constraints['weekly_minutes']}, "
            f"due_day={constraints['due_day_min']}..{constraints['due_day_max']}, "
            f"stage_count={constraints['stage_count_min']}..{constraints['stage_count_max']}, "
            f"nodes_per_stage={constraints['nodes_per_stage_min']}.."
            f"{constraints['nodes_per_stage_max']}. "
            f"Diagnostic results: {json.dumps(scored_answers, ensure_ascii=False)}"
        )
        return self._complete_model(
            user_id=user_id,
            request_id=request_id,
            operation="roadmap",
            prompt=prompt,
            response_type=_GeneratedRoadmap,
            validator=lambda roadmap: self._validate_roadmap_feasibility(
                roadmap, goal, allowed_skill_ids
            ),
        )

    def _complete_model(
        self,
        *,
        user_id: str,
        request_id: str,
        operation: str,
        prompt: str,
        response_type,
        validator: Callable[[object], None] | None = None,
    ):
        client = self._runtime_client(
            user_id=user_id,
            request_id=request_id,
            operation=operation,
        )
        candidate_prompt = prompt
        for repair_count in range(2):
            try:
                raw = client.complete(
                    role="planner",
                    prompt=candidate_prompt,
                    response_envelope=(
                        "Return one JSON object only. Do not use Markdown fences or commentary."
                    ),
                    temperature=0,
                    max_output_tokens=5000,
                    json_output=True,
                    strict_remote=True,
                )
            except EvaluationProviderError as exc:
                self._log_model_failure(
                    request_id=request_id,
                    operation=operation,
                    error_code=exc.error_code,
                    validation_stage="provider_response",
                    client=client,
                    retry_count=exc.retry_count,
                    request_latency_ms=exc.request_latency_ms,
                    total_latency_ms=exc.total_latency_ms,
                )
                if exc.error_code in {
                    "provider_response_incomplete",
                    "provider_response_invalid",
                }:
                    if repair_count == 0:
                        candidate_prompt = self._repair_prompt(prompt)
                        continue
                    raise DynamicOnboardingError(
                        "onboarding.dynamic_output_invalid",
                        "The model returned an invalid learning setup.",
                        503,
                    ) from None
                if exc.error_code in {
                    "provider_configuration_missing",
                    "provider_http_401",
                    "provider_http_402",
                    "provider_http_403",
                    "provider_http_404",
                }:
                    raise DynamicOnboardingError(
                        "onboarding.dynamic_configuration_invalid",
                        "Reasoning model configuration is unavailable.",
                        503,
                    ) from None
                raise DynamicOnboardingError(
                    "onboarding.dynamic_provider_unavailable",
                    "The model provider is temporarily unavailable.",
                    503,
                ) from None
            except Exception:
                self._log_model_failure(
                    request_id=request_id,
                    operation=operation,
                    error_code="provider.unexpected_failure",
                    validation_stage="provider_response",
                    client=client,
                )
                raise DynamicOnboardingError(
                    "onboarding.dynamic_provider_unavailable",
                    "The model provider is temporarily unavailable.",
                    503,
                ) from None
            try:
                parsed = response_type.model_validate_json(raw)
                if validator is not None:
                    validator(parsed)
                return parsed
            except _RoadmapFeasibilityError as exc:
                self._log_model_failure(
                    request_id=request_id,
                    operation=operation,
                    error_code=f"roadmap.{exc.reason_code}",
                    validation_stage="business_validation",
                    client=client,
                    retry_count=repair_count,
                )
                if repair_count == 0:
                    candidate_prompt = self._repair_prompt(
                        prompt,
                        reason_code=exc.reason_code,
                        constraints=exc.constraints,
                    )
                    continue
                raise DynamicOnboardingError(
                    (
                        "onboarding.dynamic_roadmap_infeasible"
                        if exc.reason_code in {"deadline", "weekly_budget"}
                        else "onboarding.dynamic_output_invalid"
                    ),
                    "The model could not produce a feasible learning roadmap.",
                    503,
                ) from None
            except (TypeError, ValidationError, ValueError):
                self._log_model_failure(
                    request_id=request_id,
                    operation=operation,
                    error_code="provider.output_schema_invalid",
                    validation_stage="schema_validation",
                    client=client,
                    retry_count=repair_count,
                )
                if repair_count == 0:
                    candidate_prompt = self._repair_prompt(prompt)
                    continue
                raise DynamicOnboardingError(
                    "onboarding.dynamic_output_invalid",
                    "The model returned an invalid learning setup.",
                    503,
                ) from None
        raise AssertionError("unreachable")

    @staticmethod
    def _repair_prompt(
        prompt: str,
        *,
        reason_code: str | None = None,
        constraints: dict[str, object] | None = None,
    ) -> str:
        repair = (
            f"{prompt}\nYour previous response was invalid. Return one corrected JSON object only, "
            "with every required field and no additional explanation."
        )
        if reason_code is None:
            return repair
        return (
            f"{repair} Feasibility failure: reason_code={reason_code}. Exact constraints: "
            f"{json.dumps(constraints or {}, separators=(',', ':'), sort_keys=True)}"
        )

    @staticmethod
    def _log_model_failure(
        *,
        request_id: str,
        operation: str,
        error_code: str,
        validation_stage: str,
        client: object | None = None,
        retry_count: int = 0,
        request_latency_ms: float = 0.0,
        total_latency_ms: float = 0.0,
    ) -> None:
        metadata = getattr(client, "last_completion_metadata", {}) or {}
        logger.warning(
            "dynamic onboarding model operation failed",
            extra={
                "request_id": request_id,
                "operation": operation,
                "error_code": error_code,
                "validation_stage": validation_stage,
                "model": metadata.get("model"),
                "finish_reason": metadata.get("finish_reason"),
                "retry_count": retry_count,
                "request_latency_ms": request_latency_ms,
                "total_latency_ms": total_latency_ms,
            },
        )

    @staticmethod
    def _draft_payload(draft: UserDiagnosticDraft) -> dict:
        return {
            "draft_id": draft.id,
            "expires_at": f"{draft.expires_at.isoformat()}Z",
            "title": draft.title,
            "questions": list(draft.public_questions),
        }

    @staticmethod
    def _validate_draft_answers(
        draft: UserDiagnosticDraft, request: InitializeFromDraftRequest
    ) -> dict[str, str]:
        answers: dict[str, str] = {}
        public = {question["question_id"]: question for question in draft.public_questions}
        for answer in request.knowledge_answers:
            if answer.question_id in answers:
                raise DynamicOnboardingError(
                    "onboarding.invalid_answers", "Diagnostic answers are invalid.", 422
                )
            question = public.get(answer.question_id)
            options = {option["option_id"] for option in question["options"]} if question else set()
            if answer.selected_option_id not in options:
                raise DynamicOnboardingError(
                    "onboarding.invalid_answers", "Diagnostic answers are invalid.", 422
                )
            answers[answer.question_id] = answer.selected_option_id
        if set(answers) != set(public):
            raise DynamicOnboardingError(
                "onboarding.invalid_answers", "Diagnostic answers are invalid.", 422
            )
        return answers

    def _validate_dynamic_replay(
        self,
        *,
        diagnostic: BaselineDiagnostic,
        user_id: str,
        request: InitializeFromDraftRequest,
    ) -> None:
        if (diagnostic.evidence_json or {}).get("draft_id") != request.draft_id:
            raise DynamicOnboardingError(
                "onboarding.request_conflict",
                "The request identifier is already in use.",
                409,
            )
        draft = self._session.scalar(
            select(UserDiagnosticDraft).where(
                UserDiagnosticDraft.id == request.draft_id,
                UserDiagnosticDraft.user_id == user_id,
            )
        )
        if draft is None:
            raise DynamicOnboardingError(
                "onboarding.request_conflict",
                "The request identifier is already in use.",
                409,
            )
        requested_answers = self._validate_draft_answers(draft, request)
        stored_answers = {
            item.get("question_id"): item.get("selected_option_id")
            for item in (diagnostic.submitted_answers or {}).get("knowledge_answers", [])
            if isinstance(item, dict)
        }
        if requested_answers != stored_answers:
            raise DynamicOnboardingError(
                "onboarding.request_conflict",
                "The request identifier is already in use.",
                409,
            )

    def _validate_reassess_replay(
        self,
        *,
        diagnostic: BaselineDiagnostic,
        user_id: str,
        request: ReassessFromDraftRequest,
    ) -> None:
        if diagnostic.goal_id != request.goal_id:
            raise DynamicOnboardingError(
                "onboarding.request_conflict",
                "The request identifier is already in use.",
                409,
            )
        self._validate_dynamic_replay(
            diagnostic=diagnostic,
            user_id=user_id,
            request=request,
        )

    def _load_reassess_goal(
        self, *, user_id: str, goal_id: str, for_update: bool = False
    ) -> LearningGoal:
        statement = select(LearningGoal).where(
            LearningGoal.id == goal_id,
            LearningGoal.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        goal = self._session.scalar(statement)
        if goal is None:
            raise NotFoundError(f"learning goal {goal_id} not found")
        return goal

    @staticmethod
    def _goal_input_from_goal(goal: LearningGoal) -> GoalInitializationInput:
        return GoalInitializationInput(
            title=goal.title,
            target_outcome=goal.target_outcome,
            deadline=goal.deadline,
            weekly_hours_target=goal.weekly_hours_target,
            learning_preferences=goal.learning_preferences or {},
        )

    def _validate_reassess_draft_goal(
        self,
        *,
        draft: UserDiagnosticDraft,
        goal: LearningGoal,
        locale: str,
    ) -> None:
        raw_goal_input = dict(draft.goal_input or {})
        reassess_goal_id = raw_goal_input.pop("_reassess_goal_id", None)
        try:
            stored_goal = GoalInitializationInput.model_validate(raw_goal_input)
        except ValidationError:
            stored_goal = None
        if (
            reassess_goal_id != goal.id
            or stored_goal is None
            or stored_goal.model_dump(mode="json") != self._goal_input_from_goal(goal).model_dump(mode="json")
            or draft.locale != locale
        ):
            raise DynamicOnboardingError(
                "onboarding.request_conflict",
                "The request identifier is already in use.",
                409,
            )

    @staticmethod
    def _validate_roadmap_feasibility(
        roadmap: _GeneratedRoadmap,
        goal: GoalInitializationInput,
        allowed_skill_ids: set[str] | None = None,
    ) -> None:
        allowed_skill_ids = allowed_skill_ids or set()
        constraints = OnboardingService._roadmap_constraints(goal, allowed_skill_ids)
        nodes = [
            node
            for stage in sorted(roadmap.stages, key=lambda item: item.order)
            for node in sorted(stage.nodes, key=lambda item: item.order)
        ]
        node_skill_ids = {node.skill_id for node in nodes}
        unknown_skill_ids = sorted(node_skill_ids - allowed_skill_ids)
        if allowed_skill_ids and unknown_skill_ids:
            raise _RoadmapFeasibilityError(
                "unknown_skill",
                {**constraints, "unknown_skill_ids": unknown_skill_ids},
            )
        missing_skill_ids = sorted(allowed_skill_ids - node_skill_ids)
        if missing_skill_ids:
            raise _RoadmapFeasibilityError(
                "skill_coverage",
                {**constraints, "missing_skill_ids": missing_skill_ids},
            )
        due_days = [node.due_day for node in nodes]
        if due_days != sorted(due_days):
            raise _RoadmapFeasibilityError(
                "order",
                {**constraints, "received_due_days": due_days},
            )
        if max(due_days) > constraints["due_day_max"]:
            raise _RoadmapFeasibilityError(
                "deadline",
                {**constraints, "received_due_day_max": max(due_days)},
            )
        weekly_limit = int(constraints["weekly_minutes"])
        weekly_minutes: dict[int, int] = {}
        for node in nodes:
            week = (node.due_day - 1) // 7
            weekly_minutes[week] = weekly_minutes.get(week, 0) + node.estimated_minutes
        if any(minutes > weekly_limit for minutes in weekly_minutes.values()):
            raise _RoadmapFeasibilityError(
                "weekly_budget",
                {
                    **constraints,
                    "received_weekly_minutes": {
                        str(week + 1): minutes for week, minutes in weekly_minutes.items()
                    },
                },
            )

    @staticmethod
    def _roadmap_constraints(
        goal: GoalInitializationInput, allowed_skill_ids: set[str]
    ) -> dict[str, object]:
        today = date.today()
        due_day_max = (
            min(365, (goal.deadline - today).days + 1)
            if goal.deadline is not None
            else 365
        )
        return {
            "today": today.isoformat(),
            "deadline": goal.deadline.isoformat() if goal.deadline is not None else None,
            "weekly_hours": goal.weekly_hours_target,
            "weekly_minutes": goal.weekly_hours_target * 60,
            "due_day_min": 1,
            "due_day_max": due_day_max,
            "stage_count_min": 3,
            "stage_count_max": 8,
            "nodes_per_stage_min": 1,
            "nodes_per_stage_max": 6,
            "allowed_skill_ids": sorted(allowed_skill_ids),
        }

    @staticmethod
    def _validate_goal_deadline(goal: GoalInitializationInput) -> None:
        if goal.deadline is not None and goal.deadline < date.today():
            raise DynamicOnboardingError(
                "onboarding.deadline_expired",
                "The learning goal deadline has already passed.",
                422,
            )

    @staticmethod
    def _diagnostic_skill_results(
        draft: UserDiagnosticDraft, answers: dict[str, str]
    ) -> dict[str, dict[str, object]]:
        grouped: dict[str, dict[str, object]] = {}
        for question_id, key in draft.scoring_key.items():
            skill_id = key["skill_id"]
            result = grouped.setdefault(
                skill_id,
                {
                    "skill_id": skill_id,
                    "correct": 0,
                    "question_count": 0,
                    "question_ids": [],
                },
            )
            result["correct"] = int(result["correct"]) + int(
                answers[question_id] == key["correct_option_id"]
            )
            result["question_count"] = int(result["question_count"]) + 1
            result["question_ids"].append(question_id)
        for result in grouped.values():
            question_count = int(result["question_count"])
            result["score"] = round(100 * int(result["correct"]) / question_count, 2)
            result["confidence"] = min(0.9, 0.5 + 0.1 * question_count)
        return grouped

    @staticmethod
    def _dynamic_diagnostic_trace(
        *,
        draft: UserDiagnosticDraft,
        goal_id: str,
        request_id: str,
        diagnostic_id: str,
        skill_results: dict[str, dict[str, object]],
    ) -> dict[str, object]:
        return {
            "draft_id": draft.id,
            "goal_id": goal_id,
            "request_id": request_id,
            "diagnostic_id": diagnostic_id,
            "calculation_version": "dynamic-diagnostic-skill-v1",
            "skills": [
                {
                    "skill_id": result["skill_id"],
                    "question_ids": list(result["question_ids"]),
                    "question_count": result["question_count"],
                    "correct_count": result["correct"],
                    "score": result["score"],
                    "confidence": result["confidence"],
                }
                for _, result in sorted(skill_results.items())
            ],
        }

    def _write_dynamic_mastery_memories(
        self,
        *,
        user_id: str,
        goal_id: str,
        trace: dict[str, object],
        mastery_rows: list[MasteryRecord],
    ) -> None:
        now = datetime.now(timezone.utc)
        candidates = []
        for row in mastery_rows:
            node_trace = {
                **trace,
                "knowledge_node_id": row.knowledge_node_id,
                "mastery_record_id": row.id,
            }
            command = CreateMemoryCommand(
                user_id=user_id,
                goal_id=goal_id,
                memory_type="mastery_summary",
                content={
                    "knowledge_node_id": row.knowledge_node_id,
                    "score": row.mastery_score,
                    "confidence": row.confidence,
                    "evidence_count": row.evidence_count,
                    "calculation_version": row.calculation_version,
                },
                source_kind="mastery_record",
                source_ref_id=row.id,
                source_metadata=node_trace,
                importance=0.8,
                confidence=row.confidence,
                expires_at=now + timedelta(days=30),
                idempotency_key=generated_memory_idempotency_key(
                    source_ref_id=row.id,
                    memory_type="mastery_summary",
                    semantic_key=f"mastery:{goal_id}:{row.knowledge_node_id}",
                ),
            )
            candidates.append(
                MemoryCandidate(
                    candidate_id=f"dynamic-mastery-{row.id}",
                    origin="learning_result",
                    command=command,
                    semantic_key=f"mastery:{goal_id}:{row.knowledge_node_id}",
                )
            )
        decisions = evaluate_memory_candidates(
            candidates,
            settings=MemoryPrivacyService(self._session).get(user_id=user_id, for_update=True),
            expected_user_id=user_id,
            expected_goal_id=goal_id,
            now=now,
        )
        MemoryWriteService(self._session, clock=lambda: now).save_decisions(
            user_id=user_id,
            goal_id=goal_id,
            decisions=decisions,
        )

    def _create_dynamic_mastery_adjustment(
        self,
        *,
        user_id: str,
        goal_id: str,
        plan: LearningPlan,
        initial_mastery: dict,
        skill_results: dict[str, dict[str, object]],
        trace: dict[str, object],
    ) -> PlanAdjustmentRecord | None:
        low_skills = [
            result for result in skill_results.values() if float(result["score"]) < 60
        ]
        if not low_skills:
            return None
        confidence = min(float(result["confidence"]) for result in low_skills)
        decision = decide_observer(
            ObserverSignalBundleV2(
                mastery_score=min(float(result["score"]) for result in low_skills),
                mastery_confidence=confidence,
                recent_task_count=0,
                low_prerequisite_count=len(low_skills),
                valid_sessions=0,
                has_reliable_evidence=True,
                automatic_adjustment_eligible=True,
            )
        )
        low_skill_ids = {str(result["skill_id"]) for result in low_skills}
        low_mastery_nodes = [
            {
                "knowledge_node_id": item["knowledge_node_id"],
                "score": item["score"],
            }
            for item in initial_mastery.values()
            if item.get("source_breakdown", {}).get("diagnostic", {}).get("skill_id")
            in low_skill_ids
        ]
        decision = decision.model_copy(
            update={
                "evidence_summary": {
                    **decision.evidence_summary,
                    "low_mastery_nodes": low_mastery_nodes,
                }
            }
        )
        proposal = build_plan_proposal(decision)
        if proposal.decision in {"keep", "manual_review"}:
            return None
        safe_trace = {
            **trace,
            "skills": [
                {
                    "skill_id": result["skill_id"],
                    "question_count": result["question_count"],
                    "correct_count": result["correct"],
                    "score": result["score"],
                }
                for result in low_skills
            ],
        }
        adjustment = PlanAdjustmentRecord(
            id=f"adjustment-{uuid4()}",
            user_id=user_id,
            goal_id=goal_id,
            previous_plan_id=plan.id,
            trigger_type="dynamic_diagnostic",
            decision=proposal.decision,
            evidence_json={
                "observer_signals": decision.evidence_summary,
                "diagnostic_trace": safe_trace,
            },
            before_snapshot={"active_plan_id": plan.id, "mastery_summary": initial_mastery},
            after_snapshot={"active_plan_id": plan.id, "pending_patch": proposal.plan_patch},
            plan_patch={**proposal.plan_patch, "target_skill_ids": sorted(low_skill_ids)},
            change_summary=proposal.change_summary,
            rationale_json={**proposal.rationale_json, "diagnostic_trace": safe_trace},
            status="proposed",
            policy_version=decision.policy_version,
            automation_allowed=False,
            base_plan_version=plan.version,
            risk_level="medium",
            requires_confirmation=True,
        )
        self._session.add(adjustment)
        return adjustment

    def _create_dynamic_workspace(
        self,
        *,
        user_id: str,
        request_id: str,
        draft: UserDiagnosticDraft,
        goal_input: GoalInitializationInput,
        answers: dict[str, str],
        roadmap: _GeneratedRoadmap,
        existing_goal: LearningGoal | None = None,
        existing_snapshot: LearningStateSnapshot | None = None,
        plan_version: int = 1,
    ) -> AtomicOnboardingInitializationResult:
        goal = existing_goal
        is_reassess = goal is not None
        if goal is None:
            if self._session.get(User, user_id) is None:
                raise NotFoundError(f"user {user_id} not found")
            request_like = DynamicDiagnosticDraftRequest(
                request_id=draft.request_id,
                locale=draft.locale,
                goal=goal_input,
            )
            goal = self._create_goal(user_id=user_id, request=request_like)
            goal.domain = "dynamic"
            self._upsert_profile(user_id=user_id, request=request_like)
        curriculum_uuid = uuid4()
        curriculum = Curriculum(
            id=f"curriculum-{curriculum_uuid}",
            code=f"user-{curriculum_uuid}",
            version="dynamic-v1",
            title=roadmap.title,
            domain="dynamic",
            is_active=True,
            owner_user_id=user_id,
        )
        self._session.add(curriculum)
        self._session.flush()

        skill_results = self._diagnostic_skill_results(draft, answers)
        correct_count = sum(int(result["correct"]) for result in skill_results.values())
        baseline_score = round(100 * correct_count / len(draft.scoring_key), 2)
        created_nodes: list[tuple[_GeneratedStage, _GeneratedNode, KnowledgeNode]] = []
        sequence = 0
        for stage in sorted(roadmap.stages, key=lambda item: item.order):
            for generated_node in sorted(stage.nodes, key=lambda item: item.order):
                sequence += 1
                node_uuid = uuid4()
                node = KnowledgeNode(
                    id=f"node-{node_uuid}",
                    curriculum_id=curriculum.id,
                    code=f"user_{node_uuid.hex}_{self._slug(generated_node.node_id)}",
                    title=generated_node.title,
                    sequence=sequence,
                    node_type="concept",
                    difficulty=min(5, max(1, stage.order)),
                    estimated_minutes=generated_node.estimated_minutes,
                    mastery_threshold=70,
                    metadata_json={
                        "source": "dynamic_roadmap",
                        "locale": draft.locale,
                        "stage_id": stage.stage_id,
                        "stage_title": stage.title,
                        "stage_objective": stage.objective,
                        "stage_order": stage.order,
                        "node_id": generated_node.node_id,
                        "skill_id": generated_node.skill_id,
                        "node_order": generated_node.order,
                        "objective": generated_node.objective,
                    },
                )
                self._session.add(node)
                created_nodes.append((stage, generated_node, node))
        self._session.flush()
        for index in range(1, len(created_nodes)):
            self._session.add(
                KnowledgeEdge(
                    id=f"edge-{uuid4()}",
                    curriculum_id=curriculum.id,
                    from_node_id=created_nodes[index - 1][2].id,
                    to_node_id=created_nodes[index][2].id,
                    relation_type="prerequisite",
                )
            )

        first_node = created_nodes[0][2]
        initial_mastery = {}
        for _, generated_node, node in created_nodes:
            skill_result = skill_results[generated_node.skill_id]
            initial_mastery[node.code] = {
                "knowledge_node_id": node.id,
                "node_code": node.code,
                "score": skill_result["score"],
                "confidence": skill_result["confidence"],
                "evidence_count": skill_result["question_count"],
                "self_score": None,
                "objective_score": skill_result["score"],
                "calculation_version": "dynamic-diagnostic-skill-v1",
                "source_breakdown": {
                    "diagnostic": {
                        "skill_id": generated_node.skill_id,
                        "question_ids": skill_result["question_ids"],
                        "question_count": skill_result["question_count"],
                    }
                },
            }
        knowledge_gaps = [
            {
                "node_id": node.id,
                "node_code": node.code,
                "score": initial_mastery[node.code]["score"],
                "threshold": node.mastery_threshold,
                "gap": max(0, node.mastery_threshold - initial_mastery[node.code]["score"]),
            }
            for _, _, node in created_nodes
            if initial_mastery[node.code]["score"] < node.mastery_threshold
        ]
        diagnostic = BaselineDiagnostic(
            id=f"diag-{uuid4()}",
            user_id=user_id,
            goal_id=goal.id,
            request_id=request_id,
            template_version="dynamic-v1",
            template_hash=None,
            score_breakdown={"score": baseline_score, "question_count": len(answers)},
            submitted_answers={
                "draft_id": draft.id,
                "locale": draft.locale,
                "knowledge_answers": [
                    {"question_id": key, "selected_option_id": value}
                    for key, value in answers.items()
                ],
            },
            baseline_summary=f"Start with {first_node.title}.",
            entry_node_id=first_node.id,
            knowledge_gaps=knowledge_gaps,
            initial_mastery=initial_mastery,
            evidence_json={
                "source": "dynamic_diagnostic",
                "draft_id": draft.id,
                "locale": draft.locale,
                "stage_ids": [stage.stage_id for stage in roadmap.stages],
            },
        )
        plan = LearningPlan(
            id=f"plan-{uuid4()}",
            user_id=user_id,
            goal_id=goal.id,
            curriculum_id=curriculum.id,
            version=plan_version,
            status="active",
            generated_by="dynamic_planner",
            rationale_json={"source": "dynamic_diagnostic", "draft_id": draft.id},
            valid_from=date.today(),
            valid_to=date.today()
            + timedelta(days=max(item[1].due_day for item in created_nodes) - 1),
            plan_json={
                "source": "dynamic_roadmap",
                "title": roadmap.title,
                "locale": draft.locale,
            },
        )
        self._session.add_all([diagnostic, plan])
        self._session.flush()
        tasks: list[PlanTask] = []
        for task_index, (stage, generated_node, node) in enumerate(created_nodes, start=1):
            task = PlanTask(
                id=f"task-{uuid4()}",
                plan_id=plan.id,
                user_id=user_id,
                goal_id=goal.id,
                knowledge_node_id=node.id,
                knowledge_node_code=node.code,
                title=node.title,
                task_type="study",
                objective=generated_node.objective,
                scheduled_date=date.today() + timedelta(days=generated_node.due_day - 1),
                scheduled_day=generated_node.due_day,
                estimated_minutes=generated_node.estimated_minutes,
                priority=task_index,
                status="pending",
                payload={
                    "source": "dynamic_roadmap",
                    "locale": draft.locale,
                    "stage_id": stage.stage_id,
                    "stage_order": stage.order,
                    "node_id": generated_node.node_id,
                    "node_order": generated_node.order,
                },
                origin="dynamic_planner",
            )
            tasks.append(task)
            self._session.add(task)
        self._create_mastery_records(
            user_id=user_id,
            goal_id=goal.id,
            initial_mastery=initial_mastery,
        )
        self._session.flush()
        mastery_rows = list(
            self._session.scalars(
                select(MasteryRecord).where(
                    MasteryRecord.user_id == user_id,
                    MasteryRecord.goal_id == goal.id,
                )
            )
        )
        trace = self._dynamic_diagnostic_trace(
            draft=draft,
            goal_id=goal.id,
            request_id=request_id,
            diagnostic_id=diagnostic.id,
            skill_results=skill_results,
        )
        skill_by_node_id = {
            node.id: generated_node.skill_id
            for _, generated_node, node in created_nodes
        }
        # A reassessment retains historical mastery records under the same goal.
        # Only the records created for this active plan have a matching generated skill.
        mastery_rows = [
            row for row in mastery_rows if row.knowledge_node_id in skill_by_node_id
        ]
        for row in mastery_rows:
            skill_result = skill_results[skill_by_node_id[row.knowledge_node_id]]
            row.source_breakdown = {
                **dict(row.source_breakdown or {}),
                "trace": {
                    "goal_id": goal.id,
                    "draft_id": draft.id,
                    "request_id": request_id,
                    "diagnostic_id": diagnostic.id,
                    "knowledge_node_id": row.knowledge_node_id,
                    "skill_id": skill_result["skill_id"],
                    "question_ids": list(skill_result["question_ids"]),
                    "question_count": skill_result["question_count"],
                    "correct_count": skill_result["correct"],
                    "calculation_version": "dynamic-diagnostic-skill-v1",
                },
            }
        adjustment = None
        if not is_reassess:
            self._write_dynamic_mastery_memories(
                user_id=user_id,
                goal_id=goal.id,
                trace=trace,
                mastery_rows=mastery_rows,
            )
            adjustment = self._create_dynamic_mastery_adjustment(
                user_id=user_id,
                goal_id=goal.id,
                plan=plan,
                initial_mastery=initial_mastery,
                skill_results=skill_results,
                trace=trace,
            )
        first_task = tasks[0]
        snapshot_state = {
            "today_tasks": [
                {
                    "knowledge_node_code": first_task.knowledge_node_code,
                    "title": first_task.title,
                }
            ],
            "next_action": "study",
            "review_queue": [],
            **(
                {
                    "latest_plan_adjustment": {
                        "adjustment_id": adjustment.id,
                        "decision": adjustment.decision,
                        "status": adjustment.status,
                    }
                }
                if adjustment is not None
                else {}
            ),
        }
        generated_from = {
            "baseline_diagnostic_id": diagnostic.id,
            "active_plan_id": plan.id,
            "draft_id": draft.id,
            **({"reassess_goal_id": goal.id} if is_reassess else {}),
        }
        if existing_snapshot is None:
            self._session.add(
                LearningStateSnapshot(
                id=f"snapshot-{uuid4()}",
                user_id=user_id,
                goal_id=goal.id,
                active_plan_id=plan.id,
                active_plan_version=plan.version,
                baseline_diagnostic_id=diagnostic.id,
                mastery_summary=initial_mastery,
                current_state=snapshot_state,
                generated_from=generated_from,
                latest_plan_adjustment_id=None if adjustment is None else adjustment.id,
            )
            )
        else:
            existing_snapshot.active_plan_id = plan.id
            existing_snapshot.active_plan_version = plan.version
            existing_snapshot.baseline_diagnostic_id = diagnostic.id
            existing_snapshot.mastery_summary = initial_mastery
            existing_snapshot.current_state = snapshot_state
            existing_snapshot.generated_from = generated_from
            existing_snapshot.latest_plan_adjustment_id = None
        self._session.flush()
        return self._result_from_diagnostic(diagnostic, replayed=False)

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "node"

    def initialize(
        self, *, user_id: str, request: OnboardingInitializeRequest
    ) -> AtomicOnboardingInitializationResult:
        request_id = str(request.request_id)
        try:
            existing = self._find_existing_diagnostic(
                user_id=user_id, request_id=request_id
            )
            if existing is not None:
                result = self._result_from_diagnostic(existing, replayed=True)
                self._session.commit()
                return result

            loaded = self._template_repository.load(domain="ai_app_dev")
            validate_diagnostic_answers(
                template=loaded.template,
                template_version=request.template_version,
                self_answers=request.self_assessment_answers,
                knowledge_answers=request.knowledge_answers,
            )
            user = self._session.get(User, user_id)
            if user is None:
                raise NotFoundError(f"user {user_id} not found")

            curriculum = ensure_curriculum_seeded(self._session)
            curriculum_models = ordered_nodes(self._session, curriculum.id)
            curriculum_nodes = [
                CurriculumNodeDefinition(
                    node_id=node.id,
                    code=node.code,
                    sequence=node.sequence,
                    mastery_threshold=node.mastery_threshold,
                )
                for node in curriculum_models
            ]
            scoring = score_diagnosis(
                template=loaded.template,
                self_answers=request.self_assessment_answers,
                knowledge_answers=request.knowledge_answers,
                curriculum_nodes=curriculum_nodes,
            )

            goal = self._create_goal(user_id=user_id, request=request)
            self._session.flush()
            diagnostic = self._create_diagnostic(
                user_id=user_id,
                goal=goal,
                request_id=request_id,
                request=request,
                loaded=loaded,
                scoring=scoring,
            )
            self._session.flush()
            self._upsert_profile(user_id=user_id, request=request)
            plan, task = self._create_initial_plan(
                user_id=user_id,
                goal=goal,
                curriculum_id=curriculum.id,
                scoring=scoring,
                locale=request.locale,
            )
            self._create_mastery_records(
                user_id=user_id,
                goal_id=goal.id,
                initial_mastery=diagnostic.initial_mastery,
            )
            self._create_state_snapshot(
                user_id=user_id,
                goal=goal,
                diagnostic=diagnostic,
                plan=plan,
                task=task,
            )
            self._session.flush()
            result = self._result_from_diagnostic(diagnostic, replayed=False)
            self._session.commit()
            return result
        except IntegrityError:
            self._session.rollback()
            existing = self._find_existing_diagnostic(
                user_id=user_id, request_id=request_id
            )
            if existing is None:
                self._session.rollback()
                raise
            try:
                result = self._result_from_diagnostic(existing, replayed=True)
                self._session.commit()
                return result
            except Exception:
                self._session.rollback()
                raise
        except Exception:
            self._session.rollback()
            raise

    def _find_existing_diagnostic(
        self, *, user_id: str, request_id: str
    ) -> BaselineDiagnostic | None:
        return self._session.scalar(
            select(BaselineDiagnostic).where(
                BaselineDiagnostic.user_id == user_id,
                BaselineDiagnostic.request_id == request_id,
            )
        )

    def _create_goal(
        self, *, user_id: str, request: OnboardingInitializeRequest
    ) -> LearningGoal:
        goal_input = request.goal
        preferences = goal_input.learning_preferences.model_dump(mode="json")
        goal = LearningGoal(
            id=f"goal-{uuid4()}",
            user_id=user_id,
            title=goal_input.title,
            domain="ai_app_dev",
            target_outcome=goal_input.target_outcome,
            deadline=goal_input.deadline,
            weekly_hours_target=goal_input.weekly_hours_target,
            status="active",
            learning_preferences=preferences,
        )
        self._session.add(goal)
        return goal

    def _create_diagnostic(
        self,
        *,
        user_id: str,
        goal: LearningGoal,
        request_id: str,
        request: OnboardingInitializeRequest,
        loaded: LoadedDiagnosticTemplate,
        scoring,
    ) -> BaselineDiagnostic:
        mastery = {
            node_code: item.model_dump(mode="json")
            for node_code, item in scoring.initial_mastery.items()
        }
        gaps = [gap.model_dump(mode="json") for gap in scoring.knowledge_gaps]
        score_breakdown = {
            "nodes": mastery,
            "all_baseline_nodes_passed": scoring.all_baseline_nodes_passed,
        }
        evidence_json = {
            "template_version": loaded.template.template_version,
            "template_hash": loaded.sha256,
            "all_baseline_nodes_passed": scoring.all_baseline_nodes_passed,
        }
        diagnostic = BaselineDiagnostic(
            id=f"diag-{uuid4()}",
            user_id=user_id,
            goal_id=goal.id,
            request_id=request_id,
            template_version=loaded.template.template_version,
            template_hash=loaded.sha256,
            score_breakdown=score_breakdown,
            submitted_answers={
                "template_version": request.template_version,
                "self_assessment_answers": [
                    answer.model_dump(mode="json")
                    for answer in request.self_assessment_answers
                ],
                "knowledge_answers": [
                    answer.model_dump(mode="json") for answer in request.knowledge_answers
                ],
            },
            baseline_summary=f"Start at {scoring.entry_node_code}.",
            entry_node_id=scoring.entry_node_id,
            knowledge_gaps=gaps,
            initial_mastery=mastery,
            evidence_json=evidence_json,
        )
        self._session.add(diagnostic)
        return diagnostic

    def _upsert_profile(
        self, *, user_id: str, request: OnboardingInitializeRequest
    ) -> None:
        profile = self._session.get(LearnerProfile, user_id)
        preferences = request.goal.learning_preferences.model_dump(mode="json")
        if profile is None:
            self._session.add(
                LearnerProfile(
                    user_id=user_id,
                    weekly_hours=request.goal.weekly_hours_target,
                    available_slots={},
                    learning_preferences=preferences,
                    privacy_settings={"data_scope": "v1_demo"},
                )
            )
            return
        profile.weekly_hours = request.goal.weekly_hours_target
        profile.learning_preferences = preferences

    def _create_initial_plan(self, *, user_id: str, goal, curriculum_id: str, scoring, locale: str):
        plan = LearningPlan(
            id=f"plan-{uuid4()}",
            user_id=user_id,
            goal_id=goal.id,
            curriculum_id=curriculum_id,
            version=1,
            status="active",
            generated_by="planner",
            rationale_json={
                "source": "versioned_diagnosis",
                "entry_node_code": scoring.entry_node_code,
            },
            valid_from=date.today(),
            valid_to=date.today() + timedelta(days=14),
            plan_json={"entry_node_code": scoring.entry_node_code, "horizon_days": 14},
        )
        title, objective = task_copy(locale, "study", scoring.entry_node_code)
        task = PlanTask(
            id=f"task-{uuid4()}",
            plan_id=plan.id,
            user_id=user_id,
            goal_id=goal.id,
            knowledge_node_id=scoring.entry_node_id,
            knowledge_node_code=scoring.entry_node_code,
            title=title,
            task_type="study",
            objective=objective,
            scheduled_date=date.today(),
            scheduled_day=1,
            estimated_minutes=45,
            priority=1,
            status="pending",
            payload={"source": "versioned_diagnosis"},
            origin="planner",
        )
        self._session.add_all([plan, task])
        self._session.flush()
        return plan, task

    def _create_mastery_records(
        self, *, user_id: str, goal_id: str, initial_mastery: dict
    ) -> None:
        for node_code, item in initial_mastery.items():
            self._session.add(
                MasteryRecord(
                    id=f"mastery-{uuid4()}",
                    user_id=user_id,
                    goal_id=goal_id,
                    knowledge_node_id=item["knowledge_node_id"],
                    mastery_score=item["score"],
                    confidence=item["confidence"],
                    evidence_count=item.get("evidence_count", 1),
                    source_breakdown=item.get("source_breakdown")
                    or {
                        "baseline": item["score"],
                        "self_score": item.get("self_score"),
                        "objective_score": item.get("objective_score"),
                        "node_code": node_code,
                    },
                    calculation_version=item.get(
                        "calculation_version", "phase2-mastery-v1"
                    ),
                )
            )

    def _create_state_snapshot(
        self, *, user_id: str, goal, diagnostic, plan, task
    ) -> LearningStateSnapshot:
        snapshot = LearningStateSnapshot(
            id=f"snapshot-{uuid4()}",
            user_id=user_id,
            goal_id=goal.id,
            active_plan_id=plan.id,
            active_plan_version=plan.version,
            baseline_diagnostic_id=diagnostic.id,
            mastery_summary=diagnostic.initial_mastery,
            current_state={
                "today_tasks": [
                    {
                        "knowledge_node_code": task.knowledge_node_code,
                        "title": task.title,
                    }
                ],
                "next_action": "study",
                "review_queue": [],
            },
            generated_from={
                "baseline_diagnostic_id": diagnostic.id,
                "active_plan_id": plan.id,
                "template_version": diagnostic.template_version,
            },
        )
        self._session.add(snapshot)
        return snapshot

    def _result_from_diagnostic(
        self, diagnostic: BaselineDiagnostic, *, replayed: bool
    ) -> AtomicOnboardingInitializationResult:
        goal = self._session.scalar(
            select(LearningGoal).where(
                LearningGoal.id == diagnostic.goal_id,
                LearningGoal.user_id == diagnostic.user_id,
            )
        )
        snapshot = self._session.scalar(
            select(LearningStateSnapshot).where(
                LearningStateSnapshot.user_id == diagnostic.user_id,
                LearningStateSnapshot.goal_id == diagnostic.goal_id,
            )
        )
        if goal is None or snapshot is None:
            raise RuntimeError("idempotent onboarding result is incomplete")
        state = get_current_state(
            self._session, user_id=diagnostic.user_id, goal_id=diagnostic.goal_id
        )
        diagnosis = OnboardingDiagnosisResult(
            baseline_diagnostic_id=diagnostic.id,
            entry_node_id=diagnostic.entry_node_id or "",
            entry_node_code=self._entry_node_code(diagnostic),
            baseline_summary=diagnostic.baseline_summary,
            knowledge_gaps=list(diagnostic.knowledge_gaps or []),
            initial_mastery=dict(diagnostic.initial_mastery or {}),
            evidence_json=dict(diagnostic.evidence_json or {}),
            active_plan_id=snapshot.active_plan_id,
            active_plan_version=snapshot.active_plan_version,
            template_version=diagnostic.template_version,
            template_hash=diagnostic.template_hash,
            score_breakdown=dict(diagnostic.score_breakdown or {}),
        )
        return AtomicOnboardingInitializationResult(
            goal=goal,
            diagnosis=diagnosis,
            state=state,
            replayed=replayed,
        )

    @staticmethod
    def _entry_node_code(diagnostic: BaselineDiagnostic) -> str:
        for node_code, item in (diagnostic.initial_mastery or {}).items():
            if item.get("knowledge_node_id") == diagnostic.entry_node_id:
                return node_code
        raise RuntimeError("diagnostic entry node is absent from initial mastery")
