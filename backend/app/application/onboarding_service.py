from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import re
import unicodedata
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.api.schemas.onboarding import (
    DynamicDiagnosticDraftRequest,
    GoalInitializationInput,
    InitializeFromDraftRequest,
    OnboardingInitializeRequest,
)
from backend.app.application.config_service import RuntimeResolver
from backend.app.application.task_localization import task_copy
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
    LearningStateSnapshot,
    MasteryRecord,
    PlanTask,
    User,
    UserDiagnosticDraft,
)
from backend.app.infrastructure.secrets import SecretStore
from backend.app.services.curriculum import ensure_curriculum_seeded, ordered_nodes
from backend.app.services.learning import NotFoundError, get_current_state


DEFAULT_DIAGNOSTIC_TEMPLATE_REPOSITORY = DiagnosticTemplateRepository()
_DYNAMIC_DRAFT_TTL = timedelta(hours=1)


class DynamicOnboardingError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


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
        skill_ids = [question.skill_id for question in self.questions]
        if len(question_ids) != len(set(question_ids)) or len(skill_ids) != len(set(skill_ids)):
            raise ValueError("question and skill ids must be unique")
        return self


class _GeneratedNode(_GeneratedModel):
    node_id: str = Field(min_length=1, max_length=64)
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
            roadmap = self._generate_roadmap(
                user_id=user_id,
                draft=draft,
                goal=goal_input,
                answers=answers,
            )
            self._validate_roadmap_feasibility(roadmap, goal_input)
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

    def _runtime_client(self, *, user_id: str):
        try:
            return RuntimeResolver(
                self._session,
                user_id=user_id,
                secret_store=self._secret_store,
            ).resolve("reasoning")
        except Exception:
            raise DynamicOnboardingError(
                "onboarding.dynamic_model_unavailable",
                "Dynamic learning setup is unavailable.",
                503,
            ) from None

    def _generate_diagnostic(
        self, *, user_id: str, request: DynamicDiagnosticDraftRequest
    ) -> _GeneratedDiagnostic:
        prompt = (
            f"Return strict JSON only. Write all learner-visible text in locale {request.locale}. "
            "Create 3-5 single-choice diagnostic questions for this learning goal. "
            "Schema: {title, questions:[{question_id, skill_id, prompt, "
            "options:[{option_id,label}], correct_option_id}]}. "
            f"Goal: {json.dumps(request.goal.model_dump(mode='json'), ensure_ascii=False)}"
        )
        return self._complete_model(
            user_id=user_id,
            prompt=prompt,
            response_type=_GeneratedDiagnostic,
        )

    def _generate_roadmap(
        self,
        *,
        user_id: str,
        draft: UserDiagnosticDraft,
        goal: GoalInitializationInput,
        answers: dict[str, str],
    ) -> _GeneratedRoadmap:
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
            "Use globally unique stable string ids and feasible due_day values. "
            "Schema: {title, stages:[{stage_id,title,objective,order,nodes:["
            "{node_id,title,objective,order,estimated_minutes,due_day}]}]}. "
            f"Goal: {json.dumps(goal.model_dump(mode='json'), ensure_ascii=False)}. "
            f"Diagnostic results: {json.dumps(scored_answers, ensure_ascii=False)}"
        )
        return self._complete_model(
            user_id=user_id,
            prompt=prompt,
            response_type=_GeneratedRoadmap,
        )

    def _complete_model(self, *, user_id: str, prompt: str, response_type):
        client = self._runtime_client(user_id=user_id)
        try:
            raw = client.complete(
                role="planner",
                prompt=prompt,
                response_envelope="strict_json",
                temperature=0,
                max_output_tokens=5000,
                strict_remote=True,
            )
        except Exception:
            raise DynamicOnboardingError(
                "onboarding.dynamic_model_unavailable",
                "Dynamic learning setup is unavailable.",
                503,
            ) from None
        try:
            return response_type.model_validate(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValidationError, ValueError):
            raise DynamicOnboardingError(
                "onboarding.dynamic_model_invalid",
                "The generated learning setup was invalid.",
                503,
            ) from None

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

    @staticmethod
    def _validate_roadmap_feasibility(
        roadmap: _GeneratedRoadmap, goal: GoalInitializationInput
    ) -> None:
        nodes = [node for stage in roadmap.stages for node in stage.nodes]
        due_days = [node.due_day for node in nodes]
        if due_days != sorted(due_days):
            raise DynamicOnboardingError(
                "onboarding.dynamic_model_invalid",
                "The generated learning setup was invalid.",
                503,
            )
        if goal.deadline is not None:
            days_available = (goal.deadline - date.today()).days + 1
            if days_available < 1 or max(node.due_day for node in nodes) > days_available:
                raise DynamicOnboardingError(
                    "onboarding.dynamic_model_invalid",
                    "The generated learning setup was invalid.",
                    503,
                )
        weekly_limit = goal.weekly_hours_target * 60
        weekly_minutes: dict[int, int] = {}
        for node in nodes:
            week = (node.due_day - 1) // 7
            weekly_minutes[week] = weekly_minutes.get(week, 0) + node.estimated_minutes
        if any(minutes > weekly_limit for minutes in weekly_minutes.values()):
            raise DynamicOnboardingError(
                "onboarding.dynamic_model_invalid",
                "The generated learning setup was invalid.",
                503,
            )

    def _create_dynamic_workspace(
        self,
        *,
        user_id: str,
        request_id: str,
        draft: UserDiagnosticDraft,
        goal_input: GoalInitializationInput,
        answers: dict[str, str],
        roadmap: _GeneratedRoadmap,
    ) -> AtomicOnboardingInitializationResult:
        user = self._session.get(User, user_id)
        if user is None:
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
        self._session.add_all([goal, curriculum])
        self._session.flush()

        correct_count = sum(
            answers[question_id] == key["correct_option_id"]
            for question_id, key in draft.scoring_key.items()
        )
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
        initial_mastery = {
            node.code: {
                "knowledge_node_id": node.id,
                "node_code": node.code,
                "score": baseline_score,
                "confidence": 0.5,
                "self_score": None,
                "objective_score": baseline_score,
            }
            for _, _, node in created_nodes
        }
        knowledge_gaps = [
            {
                "node_id": node.id,
                "node_code": node.code,
                "score": baseline_score,
                "threshold": node.mastery_threshold,
                "gap": max(0, node.mastery_threshold - baseline_score),
            }
            for _, _, node in created_nodes
            if baseline_score < node.mastery_threshold
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
            version=1,
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
        first_task = tasks[0]
        self._session.add(
            LearningStateSnapshot(
                id=f"snapshot-{uuid4()}",
                user_id=user_id,
                goal_id=goal.id,
                active_plan_id=plan.id,
                active_plan_version=plan.version,
                baseline_diagnostic_id=diagnostic.id,
                mastery_summary=initial_mastery,
                current_state={
                    "today_tasks": [
                        {
                            "knowledge_node_code": first_task.knowledge_node_code,
                            "title": first_task.title,
                        }
                    ],
                    "next_action": "study",
                    "review_queue": [],
                },
                generated_from={
                    "baseline_diagnostic_id": diagnostic.id,
                    "active_plan_id": plan.id,
                    "draft_id": draft.id,
                },
            )
        )
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
                    evidence_count=1,
                    source_breakdown={
                        "baseline": item["score"],
                        "self_score": item.get("self_score"),
                        "objective_score": item.get("objective_score"),
                        "node_code": node_code,
                    },
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
