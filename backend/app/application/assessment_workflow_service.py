from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from adaptive_tutor.phase2.assessment_v2 import AssessmentV2WorkflowPorts, Phase2AssessmentV2Workflow

from backend.app.api.schemas.assessment_results import (
    AssessmentAnswerPublicResult,
    AssessmentSubmissionPublicResponse,
    MasteryUpdatePublic,
    ObserverDecisionPublic,
    PlanAdjustmentPublic,
    PublicGradingMetadata,
)
from backend.app.api.schemas.assessments import AssessmentItemPublic, AssessmentOptionPublic, AssessmentPublicResponse, PhaseAssessmentPublicResponse
from backend.app.application.assessment_context_service import AssessmentContextService, canonical_json_hash, normalize_answers
from backend.app.application.assessment_generation_service import AssessmentGenerationService
from backend.app.application.assessment_grading_service import AssessmentGradingService
from backend.app.domain.assessment.contracts import MasteryEvidenceV2, ObserverSignalBundleV2
from backend.app.domain.assessment.errors import AssessmentConflict, AssessmentDomainError, AssessmentUnavailable
from backend.app.domain.assessment.grading_policy import overall_score, score_item
from backend.app.domain.assessment.mastery_policy import GRADING_MODE_WEIGHT, QUESTION_TYPE_WEIGHT, calculate_mastery_updates
from backend.app.domain.assessment.observer_policy import decide_observer
from backend.app.domain.assessment.plan_policy import build_plan_proposal
from backend.app.infrastructure.persistence.repositories.assessment_repository import upsert_phase_state
from backend.app.infrastructure.persistence.repositories.assessment_v2_repository import AssessmentV2Repository, SubmissionClaim
from backend.app.models import (
    AgentRun,
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    LearningEvent,
    LearningStateSnapshot,
    MasteryRecord,
    PhaseAssessmentState,
    PlanAdjustmentRecord,
    PlanTask,
)


class AssessmentWorkflowService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.contexts = AssessmentContextService(session)
        self.repository = AssessmentV2Repository(session)

    def create(
        self,
        *,
        user_id: str,
        request_id: str,
        goal_id: str,
        thread_id: str,
        assessment_type: str,
        knowledge_node_ids: list[str],
        phase_code: str | None = None,
    ) -> AssessmentPublicResponse | PhaseAssessmentPublicResponse:
        request_payload = {
            "goal_id": goal_id,
            "thread_id": thread_id,
            "assessment_type": assessment_type,
            "knowledge_node_ids": knowledge_node_ids,
            "phase_code": phase_code,
        }
        input_hash = canonical_json_hash(request_payload)
        existing = self.repository.find_generation(user_id=user_id, request_id=request_id)
        if existing is not None:
            if existing.generation_input_hash != input_hash:
                raise AssessmentConflict("The request ID was already used with a different assessment payload.")
            return self._public_assessment(existing, phase_code=phase_code)

        context = self.contexts.build_generation(
            user_id=user_id,
            goal_id=goal_id,
            assessment_type=assessment_type,
            knowledge_node_ids=knowledge_node_ids,
        )
        self.session.rollback()
        outcome = self._generate_with_prepared_context(context)
        try:
            existing = self.repository.find_generation(user_id=user_id, request_id=request_id)
            if existing is not None:
                if existing.generation_input_hash != input_hash:
                    raise AssessmentConflict("The request ID was already used with a different assessment payload.")
                self.session.rollback()
                return self._public_assessment(existing, phase_code=phase_code)
            assessment = self.repository.save_generation(
                user_id=user_id,
                goal_id=goal_id,
                assessment_type=assessment_type,
                request_id=request_id,
                input_hash=input_hash,
                bundle=outcome.bundle,
                outcome=outcome,
                knowledge_node_ids=knowledge_node_ids,
            )
            if phase_code is not None:
                upsert_phase_state(
                    self.session,
                    user_id=user_id,
                    goal_id=goal_id,
                    assessment_id=assessment.id,
                    phase_code=phase_code,
                    knowledge_node_ids=knowledge_node_ids,
                    status="active",
                )
            self._record_generation_audit(assessment=assessment, thread_id=thread_id)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.repository.find_generation(user_id=user_id, request_id=request_id)
            if existing is None or existing.generation_input_hash != input_hash:
                raise
            self.session.rollback()
            return self._public_assessment(existing, phase_code=phase_code)
        return self._public_assessment(assessment, phase_code=phase_code)

    def submit(
        self,
        *,
        assessment_id: str,
        user_id: str,
        request_id: str,
        answers: dict[str, str],
    ) -> AssessmentSubmissionPublicResponse:
        normalized_answers = normalize_answers(answers)
        answer_hash = canonical_json_hash(normalized_answers)
        claim = self._claim_submission(
            assessment_id=assessment_id,
            user_id=user_id,
            request_id=request_id,
            answer_hash=answer_hash,
            answers=normalized_answers,
        )
        if isinstance(claim, AssessmentSubmissionPublicResponse):
            return claim
        try:
            self.session.expire_all()
            assessment = self.session.get(Assessment, claim.assessment_id)
            attempt = self.session.get(AssessmentAttempt, claim.attempt_id)
            if assessment is None or attempt is None:
                raise RuntimeError("submission claim disappeared")
            grading_context = self.contexts.build_grading(assessment=assessment, attempt=attempt)
            self.session.commit()
            grading = self._grade_with_prepared_context(grading_context)
            return self._finalize_submission(claim=claim, grading_context=grading_context, grading=grading)
        except Exception as exc:
            self._fail_claim(claim, error_code=_error_code(exc))
            raise

    def _claim_submission(
        self,
        *,
        assessment_id: str,
        user_id: str,
        request_id: str,
        answer_hash: str,
        answers: dict[str, str],
    ) -> SubmissionClaim | AssessmentSubmissionPublicResponse:
        assessment = self.repository.lock_assessment(assessment_id=assessment_id, user_id=user_id)
        if assessment is None:
            self.session.rollback()
            raise LookupError(f"assessment {assessment_id} not found")
        self.contexts.validate_answer_item_ids(assessment_id, answers)
        existing = self.repository.lock_attempt_by_request(assessment_id=assessment_id, user_id=user_id, request_id=request_id)
        if existing is not None:
            if existing.answer_payload_hash != answer_hash:
                self.session.rollback()
                raise AssessmentConflict("The request ID was already used with a different answer payload.")
            stored = (existing.grading_metadata or {}).get("public_response")
            if existing.status in {"graded", "review_required"} and isinstance(stored, dict):
                self.session.rollback()
                return AssessmentSubmissionPublicResponse.model_validate(stored)
            if existing.status == "grading" and not _lease_expired(existing.lease_expires_at):
                self.session.rollback()
                raise AssessmentDomainError("Assessment grading is already in progress.", code="assessment.grading_in_progress", status_code=409)
            if existing.attempt_count >= _grading_max_attempts():
                existing.status = "failed"
                existing.error_code = "assessment.grading_unavailable"
                assessment.status = "active"
                self.session.commit()
                raise AssessmentUnavailable("Assessment grading attempts were exhausted.", code="assessment.grading_unavailable")
            claim = self.repository.reclaim_claim(attempt=existing, assessment=assessment, lease_seconds=_lease_seconds())
            self.session.commit()
            return claim
        if assessment.status in {"graded", "review_required", "submitted"}:
            self.session.rollback()
            raise AssessmentDomainError("Assessment was already submitted.", code="assessment.request_id_payload_conflict", status_code=409)
        active = self.repository.active_attempt(assessment_id=assessment_id)
        if active is not None and active.status == "grading" and not _lease_expired(active.lease_expires_at):
            self.session.rollback()
            raise AssessmentDomainError("Assessment grading is already in progress.", code="assessment.grading_in_progress", status_code=409)
        if not self.repository.claim_assessment_if_active(assessment_id=assessment_id, user_id=user_id):
            self.session.rollback()
            raise AssessmentDomainError("Assessment was already submitted.", code="assessment.request_id_payload_conflict", status_code=409)
        assessment.status = "grading"
        claim = self.repository.create_claim(
            assessment=assessment,
            user_id=user_id,
            request_id=request_id,
            answer_hash=answer_hash,
            answers=answers,
            lease_seconds=_lease_seconds(),
        )
        self.session.commit()
        return claim

    @staticmethod
    def _generate_with_prepared_context(context):
        outcomes = []

        def generate(prepared):
            outcome = AssessmentGenerationService().generate(prepared)
            outcomes.append(outcome)
            return outcome.bundle

        result = Phase2AssessmentV2Workflow(AssessmentV2WorkflowPorts(generator=generate)).run_generation(context)
        if result.generation_bundle is None or not outcomes:
            raise RuntimeError("assessment generation workflow returned no bundle")
        return outcomes[0]

    @staticmethod
    def _grade_with_prepared_context(context):
        outcomes = []

        def generate(_: object):
            raise RuntimeError("generation is not available in the grading workflow")

        def grade(prepared):
            outcome = AssessmentGradingService().grade(prepared)
            outcomes.append(outcome)
            return outcome.bundle

        result = Phase2AssessmentV2Workflow(AssessmentV2WorkflowPorts(generator=generate, grader=grade)).run_grading(context)
        if result.grade_bundle is None or not outcomes:
            raise RuntimeError("assessment grading workflow returned no bundle")
        return outcomes[0]

    def _finalize_submission(self, *, claim: SubmissionClaim, grading_context, grading) -> AssessmentSubmissionPublicResponse:
        attempt = self.session.scalar(select(AssessmentAttempt).where(AssessmentAttempt.id == claim.attempt_id).with_for_update())
        assessment = self.session.scalar(select(Assessment).where(Assessment.id == claim.assessment_id).with_for_update())
        if attempt is None or assessment is None or attempt.status != "grading" or attempt.claim_token != claim.claim_token:
            self.session.rollback()
            raise AssessmentDomainError("Assessment grading lease no longer belongs to this request.", code="assessment.grading_lease_conflict", status_code=409)
        if attempt.answer_payload_hash != canonical_json_hash(grading_context.submitted_answers):
            self.session.rollback()
            raise AssessmentConflict("Submission payload changed while grading.")
        item_by_id = {item.item_id: item for item in grading_context.items}
        grade_by_id = {grade.item_id: grade for grade in grading.bundle.item_grades}
        item_scores = {item_id: score_item(grade_by_id[item_id], item) for item_id, item in item_by_id.items()}
        needs_review = any(grade.needs_human_review for grade in grading.bundle.item_grades)
        status = "review_required" if needs_review else "graded"
        result_score = overall_score(grading.bundle, grading_context)
        evidence = self._build_evidence(assessment.id, attempt.id, grading_context, grading, item_scores)
        records = list(
            self.session.scalars(
                select(MasteryRecord)
                .where(MasteryRecord.user_id == attempt.user_id, MasteryRecord.goal_id == assessment.goal_id)
                .with_for_update()
            )
        )
        records_by_node = {record.knowledge_node_id: record for record in records}
        previous = {
            node_id: {
                "score": record.mastery_score,
                "confidence": record.confidence,
                "evidence_weight": (record.source_breakdown or {}).get("assessment_v2", {}).get("evidence_weight", 0),
                "last_evidence_at": record.last_evidence_at,
            }
            for node_id, record in records_by_node.items()
        }
        updates = calculate_mastery_updates(previous, evidence)
        self._persist_mastery(attempt.user_id, assessment.goal_id, updates, evidence, records_by_node)
        snapshot = self.session.scalar(
            select(LearningStateSnapshot)
            .where(LearningStateSnapshot.user_id == attempt.user_id, LearningStateSnapshot.goal_id == assessment.goal_id)
            .with_for_update()
        )
        phase_state = self.session.scalar(select(PhaseAssessmentState).where(PhaseAssessmentState.assessment_id == assessment.id).with_for_update())
        decision = self._observer_decision(
            assessment=assessment,
            updates=updates,
            needs_review=needs_review,
            phase_state=phase_state,
            user_id=attempt.user_id,
            goal_id=assessment.goal_id,
        )
        proposal = build_plan_proposal(decision)
        adjustment = PlanAdjustmentRecord(
            id=f"adjustment-{uuid4()}",
            user_id=attempt.user_id,
            goal_id=assessment.goal_id,
            previous_plan_id=snapshot.active_plan_id if snapshot else assessment.plan_id,
            trigger_type="assessment_submitted",
            decision=proposal.decision,
            evidence_json={"observer_signals": decision.evidence_summary, "reason_codes": decision.reason_codes},
            before_snapshot={"active_plan_id": snapshot.active_plan_id if snapshot else assessment.plan_id},
            after_snapshot={"active_plan_id": snapshot.active_plan_id if snapshot else assessment.plan_id, "pending_patch": proposal.plan_patch},
            plan_patch=proposal.plan_patch,
            change_summary=proposal.change_summary,
            rationale_json=proposal.rationale_json,
            status="proposed",
            policy_version=decision.policy_version,
            automation_allowed=proposal.automation_allowed,
        )
        self.session.add(adjustment)
        answer_models: list[AssessmentAnswer] = []
        for item_id, item in item_by_id.items():
            grade = grade_by_id[item_id]
            mode = grading.item_modes[item_id]
            answer_model = AssessmentAnswer(
                id=f"answer-{uuid4()}",
                attempt_id=attempt.id,
                item_id=item_id,
                answer_text=grading_context.submitted_answers.get(item_id, ""),
                answer_json={"length": len(grading_context.submitted_answers.get(item_id, ""))},
                score=item_scores[item_id] or 0,
                grader_type=mode,
                grader_reason=grade.feedback,
                evidence_json={
                    "schema_version": "assessment-grade-v2",
                    "criterion_grades": [criterion.model_dump() for criterion in grade.criterion_grades],
                    "wrong_reason_tags": grade.wrong_reason_tags,
                    "eligible_for_mastery": item_scores[item_id] is not None and not grade.needs_human_review,
                },
                confidence=grade.confidence,
                needs_review=grade.needs_human_review,
            )
            self.session.add(answer_model)
            answer_models.append(answer_model)
        if phase_state is not None:
            phase_state.status = status
            phase_state.readiness_score = result_score or 0
            phase_state.last_result_json = {"score": result_score, "status": status, "attempt_id": attempt.id}
            phase_state.next_action = decision.decision
        if snapshot is not None:
            mastery_summary = dict(snapshot.mastery_summary or {})
            for update in updates:
                mastery_summary[update.knowledge_node_id] = {"knowledge_node_id": update.knowledge_node_id, "score": update.new_score, "confidence": update.new_confidence}
            snapshot.mastery_summary = mastery_summary
            state = dict(snapshot.current_state or {})
            state["review_queue"] = [
                {"knowledge_node_id": update.knowledge_node_id, "reason": "assessment_score_below_threshold"}
                for update in updates
                if update.new_score < 70
            ]
            state["latest_plan_adjustment"] = {"adjustment_id": adjustment.id, "decision": adjustment.decision, "status": "proposed"}
            snapshot.current_state = state
            snapshot.latest_plan_adjustment_id = adjustment.id
        assessment.status = status
        assessment.total_score = result_score
        attempt.status = status
        attempt.score = result_score
        attempt.feedback = grading.bundle.overall_feedback
        attempt.grader_mode = _overall_mode(grading.item_modes)
        attempt.grader_version = grading.bundle.grader_version
        attempt.grader_model = grading.model
        attempt.grading_confidence = _grading_confidence(grading.bundle)
        attempt.completed_at = datetime.now(timezone.utc)
        attempt.lease_expires_at = None
        attempt.claim_token = None
        attempt.grading_metadata = _safe_grading_metadata(grading.metadata)
        response = self._submission_response(
            assessment=assessment,
            attempt=attempt,
            answer_models=answer_models,
            updates=updates,
            decision=decision,
            adjustment=adjustment,
            needs_review=needs_review,
        )
        attempt.grading_metadata["public_response"] = response.model_dump(mode="json")
        self.session.add(
            LearningEvent(
                id=f"event-{uuid4()}",
                user_id=attempt.user_id,
                goal_id=assessment.goal_id,
                task_id=None,
                session_id=None,
                event_type="assessment_submitted",
                source="assessment_v2",
                event_payload={"assessment_id": assessment.id, "attempt_id": attempt.id, "score": result_score, "status": status},
            )
        )
        self.session.add(
            AgentRun(
                id=f"agent-run-{uuid4()}",
                user_id=attempt.user_id,
                thread_id="assessment-submit",
                graph_name="assessment_v2_workflow",
                graph_version="assessment-v2",
                trigger_type="assessment_submitted",
                input_snapshot={"assessment_id": assessment.id, "attempt_id": attempt.id, "input_hash": grading_context.context_hash},
                output_snapshot={"status": status, "score": result_score, "mode": attempt.grader_mode},
                status="success",
                latency_ms=0,
                error_message=None,
            )
        )
        self.session.commit()
        return response

    def _build_evidence(self, assessment_id, attempt_id, context, grading, item_scores):
        now = datetime.now(timezone.utc)
        evidence: list[MasteryEvidenceV2] = []
        for item in context.items:
            grade = next(value for value in grading.bundle.item_grades if value.item_id == item.item_id)
            mode = grading.item_modes[item.item_id]
            score = item_scores[item.item_id]
            confidence = grade.confidence
            weight = QUESTION_TYPE_WEIGHT[item.question_type] * GRADING_MODE_WEIGHT[mode] * confidence
            evidence.append(
                MasteryEvidenceV2(
                    knowledge_node_id=item.knowledge_node_id,
                    assessment_id=assessment_id,
                    attempt_id=attempt_id,
                    item_id=item.item_id,
                    question_type=item.question_type,
                    score=score if score is not None else 0,
                    grader_confidence=confidence,
                    grading_mode=mode,
                    reliability_weight=weight,
                    eligible_for_mastery=score is not None and not grade.needs_human_review,
                    wrong_reason_tags=grade.wrong_reason_tags,
                    occurred_at=now,
                )
            )
        return evidence

    def _persist_mastery(self, user_id, goal_id, updates, evidence, records_by_node) -> None:
        evidence_by_node: dict[str, list[MasteryEvidenceV2]] = {}
        for item in evidence:
            evidence_by_node.setdefault(item.knowledge_node_id, []).append(item)
        for update in updates:
            record = records_by_node.get(update.knowledge_node_id)
            weight = update.source_breakdown["previous_evidence_weight"] + update.total_evidence_weight
            last_evidence = max((item.occurred_at for item in evidence_by_node.get(update.knowledge_node_id, []) if item.eligible_for_mastery), default=None)
            breakdown = {**update.source_breakdown, "assessment_v2": {"evidence_weight": weight, "accepted": update.accepted_evidence_count, "rejected": update.rejected_evidence_count}}
            if record is None:
                record = MasteryRecord(
                    id=f"mastery-{uuid4()}", user_id=user_id, goal_id=goal_id, knowledge_node_id=update.knowledge_node_id,
                    mastery_score=update.new_score, confidence=update.new_confidence, evidence_count=update.accepted_evidence_count,
                    source_breakdown=breakdown, calculation_version="mastery-v2", last_evidence_at=last_evidence,
                )
                self.session.add(record)
            else:
                record.mastery_score = update.new_score
                record.confidence = update.new_confidence
                record.evidence_count += update.accepted_evidence_count
                record.source_breakdown = breakdown
                record.calculation_version = "mastery-v2"
                if last_evidence is not None:
                    record.last_evidence_at = last_evidence

    def _observer_decision(self, *, assessment, updates, needs_review, phase_state, user_id, goal_id):
        tasks = list(self.session.scalars(select(PlanTask).where(PlanTask.user_id == user_id, PlanTask.goal_id == goal_id).limit(7)))
        completed = sum(task.status in {"completed", "done"} for task in tasks)
        completion = completed / len(tasks) if tasks else None
        return decide_observer(
            ObserverSignalBundleV2(
                phase_status=phase_state.status if phase_state else None,
                readiness_score=phase_state.readiness_score if phase_state else None,
                mastery_score=min((update.new_score for update in updates), default=None),
                mastery_confidence=min((update.new_confidence for update in updates), default=0.1),
                completion_rate_7d=completion,
                recent_task_count=len(tasks),
                low_prerequisite_count=0,
                valid_sessions=1,
                repeated_misconceptions=[],
                needs_human_review=needs_review,
                has_reliable_evidence=any(update.accepted_evidence_count for update in updates),
                automatic_adjustment_eligible=all(update.automatic_adjustment_eligible for update in updates) if updates else False,
            )
        )

    def _fail_claim(self, claim: SubmissionClaim, *, error_code: str) -> None:
        self.session.rollback()
        try:
            attempt = self.session.scalar(select(AssessmentAttempt).where(AssessmentAttempt.id == claim.attempt_id).with_for_update())
            assessment = self.session.scalar(select(Assessment).where(Assessment.id == claim.assessment_id).with_for_update())
            if attempt is not None and assessment is not None and attempt.status == "grading" and attempt.claim_token == claim.claim_token:
                attempt.status = "failed"
                attempt.error_code = error_code
                attempt.claim_token = None
                attempt.lease_expires_at = None
                assessment.status = "active"
                self.session.commit()
            else:
                self.session.rollback()
        except Exception:
            self.session.rollback()

    def _public_assessment(self, assessment: Assessment, *, phase_code: str | None = None):
        items = self.repository.load_items(assessment.id)
        public = AssessmentPublicResponse(
            assessment_id=assessment.id,
            assessment_type=assessment.assessment_type,
            status="active",
            scope={"knowledge_node_ids": list((assessment.scope or {}).get("knowledge_node_ids", []))},
            items=[
                AssessmentItemPublic(
                    item_id=item.id,
                    knowledge_node_id=item.knowledge_node_id,
                    question_type=item.question_type,
                    prompt=item.prompt,
                    options=[AssessmentOptionPublic(option_id=option["option_id"], label=option["label"]) for option in (item.options_json or {}).get("options", [])],
                    difficulty=item.difficulty,
                )
                for item in items
            ],
        )
        if phase_code is None:
            return public
        state = self.session.scalar(select(PhaseAssessmentState).where(PhaseAssessmentState.assessment_id == assessment.id))
        if state is None:
            return public
        return PhaseAssessmentPublicResponse(**public.model_dump(), phase_assessment_state_id=state.id, phase_code=phase_code)

    @staticmethod
    def _submission_response(*, assessment, attempt, answer_models, updates, decision, adjustment, needs_review):
        status = "review_required" if needs_review else "graded"
        return AssessmentSubmissionPublicResponse(
            assessment_id=assessment.id,
            attempt_id=attempt.id,
            status=status,
            score=attempt.score,
            feedback=attempt.feedback,
            grading=PublicGradingMetadata(
                mode=attempt.grader_mode,
                grader_version=attempt.grader_version,
                confidence=attempt.grading_confidence,
                needs_review=needs_review,
                automatic_mastery_eligible=all(update.automatic_adjustment_eligible for update in updates) if updates else False,
            ),
            answers=[
                AssessmentAnswerPublicResult(
                    item_id=answer.item_id,
                    score=None if answer.needs_review else answer.score,
                    feedback=answer.grader_reason,
                    wrong_reason_tags=list((answer.evidence_json or {}).get("wrong_reason_tags", [])),
                    confidence=answer.confidence,
                    needs_review=answer.needs_review,
                )
                for answer in answer_models
            ],
            mastery_updates=[
                MasteryUpdatePublic(
                    knowledge_node_id=update.knowledge_node_id,
                    previous_score=update.previous_score,
                    new_score=update.new_score,
                    new_confidence=update.new_confidence,
                    automatic_adjustment_eligible=update.automatic_adjustment_eligible,
                    reason_codes=update.reason_codes,
                )
                for update in updates
            ],
            observer_decision=ObserverDecisionPublic(
                policy_version=decision.policy_version,
                decision=decision.decision,
                automation_allowed=decision.automation_allowed,
                confidence=decision.confidence,
                reason_codes=decision.reason_codes,
                user_facing_rationale=decision.user_facing_rationale,
            ),
            plan_adjustment=PlanAdjustmentPublic(
                adjustment_id=adjustment.id,
                decision=adjustment.decision,
                status="proposed",
                automation_allowed=adjustment.automation_allowed,
                change_summary=adjustment.change_summary,
                rationale=str((adjustment.rationale_json or {}).get("rationale", "")),
            ),
        )

    def _record_generation_audit(self, *, assessment: Assessment, thread_id: str) -> None:
        self.session.add(
            AgentRun(
                id=f"agent-run-{uuid4()}", user_id=assessment.user_id, thread_id=thread_id,
                graph_name="assessment_v2_workflow", graph_version="assessment-v2", trigger_type="assessment_due",
                input_snapshot={"assessment_id": assessment.id, "input_hash": assessment.generation_input_hash},
                output_snapshot={"mode": assessment.generation_mode, "item_count": len(self.repository.load_items(assessment.id))},
                status="success", latency_ms=0, error_message=None,
            )
        )


def _lease_expired(value: datetime | None) -> bool:
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= datetime.now(timezone.utc)


def _lease_seconds() -> int:
    return _env_int("ASSESSMENT_GRADING_LEASE_SECONDS", 120, minimum=1)


def _grading_max_attempts() -> int:
    return _env_int("ASSESSMENT_GRADING_MAX_ATTEMPTS", 3, minimum=1)


def _env_int(name: str, default: int, *, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _overall_mode(item_modes: dict[str, str]) -> str:
    modes = set(item_modes.values())
    if "manual_review_required" in modes:
        return "manual_review_required"
    if "remote_structured" in modes:
        return "remote_structured"
    if "deterministic_fallback" in modes:
        return "deterministic_fallback"
    return "deterministic_exact"


def _grading_confidence(bundle) -> float | None:
    if not bundle.item_grades:
        return None
    return round(sum(item.confidence for item in bundle.item_grades) / len(bundle.item_grades), 4)


def _safe_grading_metadata(metadata: dict[str, object]) -> dict[str, object]:
    permitted = {"mode", "model", "prompt_version", "latency_ms", "retry_count", "repair_count", "error_code", "input_hash", "output_hash"}
    return {key: value for key, value in metadata.items() if key in permitted}


def _error_code(error: Exception) -> str:
    if isinstance(error, AssessmentDomainError):
        return error.code
    return "assessment.grading_unavailable"
