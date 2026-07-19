from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.api.schemas.onboarding import OnboardingInitializeRequest
from backend.app.domain.diagnosis.contracts import CurriculumNodeDefinition
from backend.app.domain.diagnosis.scoring import score_diagnosis
from backend.app.domain.diagnosis.validation import validate_diagnostic_answers
from backend.app.infrastructure.diagnosis.template_repository import (
    DiagnosticTemplateRepository,
    LoadedDiagnosticTemplate,
)
from backend.app.models import (
    BaselineDiagnostic,
    LearnerProfile,
    LearningGoal,
    LearningPlan,
    LearningStateSnapshot,
    MasteryRecord,
    PlanTask,
    User,
)
from backend.app.services.curriculum import ensure_curriculum_seeded, ordered_nodes
from backend.app.services.learning import NotFoundError, get_current_state


DEFAULT_DIAGNOSTIC_TEMPLATE_REPOSITORY = DiagnosticTemplateRepository()


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
    ) -> None:
        self._session = session
        self._template_repository = (
            template_repository or DEFAULT_DIAGNOSTIC_TEMPLATE_REPOSITORY
        )

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

    def _create_initial_plan(self, *, user_id: str, goal, curriculum_id: str, scoring):
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
        task = PlanTask(
            id=f"task-{uuid4()}",
            plan_id=plan.id,
            user_id=user_id,
            goal_id=goal.id,
            knowledge_node_id=scoring.entry_node_id,
            knowledge_node_code=scoring.entry_node_code,
            title=f"Study {scoring.entry_node_code}",
            task_type="study",
            objective=f"Build confidence on {scoring.entry_node_code.replace('_', ' ')}.",
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
