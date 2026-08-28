from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import AssessmentDraft, TutorRunRequest
from backend.app.application.engine import _resolve_tutor_request_thread, _run_engine
from backend.app.application.learning_activity_service import (
    _load_goal_for_user,
    _record_learning_event,
    _refresh_activity_state,
)
from backend.app.api.schemas.assessments import (
    AssessmentPublicResponse,
    PhaseAssessmentPublicResponse,
)
from backend.app.application.serialization import assessment_draft_to_public
from backend.app.api.schemas.assessment_results import (
    AssessmentAnswerPublicResult,
    AssessmentSubmissionPublicResponse,
    MasteryUpdatePublic,
    ObserverDecisionPublic,
    PublicGradingMetadata,
)
from backend.app.core.exceptions import (
    AssessmentAnswerValidationError,
    AssessmentSubmissionConflict,
)
from backend.app.domain.assessment.errors import AssessmentConflict
from backend.app.infrastructure.persistence.repositories.assessment_repository import (
    SQLAlchemyAssessmentRepository,
    refresh_phase_state_after_submit,
    upsert_phase_state,
)
from backend.app.infrastructure.persistence.repositories.state_repository import SQLAlchemyStateRepository
from backend.app.models import Assessment, KnowledgeNode
from backend.app.models import AssessmentAnswer, AssessmentAttempt
from adaptive_tutor.tutor.t3_contracts import canonical_json_hash


def create_assessment(
    session: Session,
    *,
    user_id: str,
    request_id: str,
    goal_id: str,
    thread_id: str,
    assessment_type: str,
    locale: str,
    knowledge_node_ids: list[str],
) -> AssessmentPublicResponse:
    request_payload = {
        "goal_id": goal_id,
        "thread_id": thread_id,
        "assessment_type": assessment_type,
        "locale": locale,
        "knowledge_node_ids": knowledge_node_ids,
    }
    input_hash = canonical_json_hash(request_payload)
    existing = session.scalar(
        select(Assessment).where(
            Assessment.user_id == user_id,
            Assessment.generation_request_id == request_id,
        )
    )
    if existing is not None:
        if existing.generation_input_hash != input_hash:
            raise AssessmentConflict("The request ID was already used with a different assessment payload.")
        draft = SQLAlchemyAssessmentRepository(session, user_id, goal_id).get_assessment_draft(existing.id)
        return assessment_draft_to_public(draft)
    _load_goal_for_user(session, user_id=user_id, goal_id=goal_id)
    request = _resolve_tutor_request_thread(
        session,
        TutorRunRequest(
            trigger_type="assessment_due",
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            assessment_type=assessment_type,
            knowledge_node_ids=knowledge_node_ids,
            metadata={
                "locale": locale,
                "knowledge_node_labels": _knowledge_node_labels(session, knowledge_node_ids),
            },
        ),
    )
    result = _run_engine(
        session,
        request,
    )
    if result.assessment_draft is None:
        raise RuntimeError("phase2 engine did not return an assessment draft")
    assessment = session.get(Assessment, result.assessment_draft.assessment_id)
    if assessment is None:
        raise RuntimeError("phase2 engine did not persist the assessment draft")
    assessment.generation_request_id = request_id
    assessment.generation_input_hash = input_hash
    session.commit()
    return assessment_draft_to_public(result.assessment_draft)

def create_phase_assessment(
    session: Session,
    *,
    user_id: str,
    request_id: str,
    goal_id: str,
    thread_id: str,
    phase_code: str,
    locale: str,
    knowledge_node_ids: list[str],
) -> PhaseAssessmentPublicResponse:
    _load_goal_for_user(session, user_id=user_id, goal_id=goal_id)
    request = _resolve_tutor_request_thread(
        session,
        TutorRunRequest(
            trigger_type="assessment_due",
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            assessment_type="phase",
            knowledge_node_ids=knowledge_node_ids,
            metadata={
                "locale": locale,
                "knowledge_node_labels": _knowledge_node_labels(session, knowledge_node_ids),
            },
        ),
    )
    result = _run_engine(
        session,
        request,
    )
    if result.assessment_draft is None:
        raise RuntimeError("phase2 engine did not return a phase assessment draft")
    phase_state = upsert_phase_state(
        session,
        user_id=user_id,
        goal_id=goal_id,
        assessment_id=result.assessment_draft.assessment_id,
        phase_code=phase_code,
        knowledge_node_ids=knowledge_node_ids,
        status="active",
    )
    SQLAlchemyStateRepository(session).refresh_snapshot(
        user_id,
        goal_id,
        {
            "phase_assessment_state_id": phase_state.id,
            "generated_from": {"phase_assessment_state_id": phase_state.id},
        },
    )
    session.commit()
    return PhaseAssessmentPublicResponse(
        **assessment_draft_to_public(result.assessment_draft).model_dump(),
        phase_assessment_state_id=phase_state.id,
        phase_code=phase_code,
    )


def _knowledge_node_labels(session: Session, knowledge_node_ids: list[str]) -> dict[str, str]:
    if not knowledge_node_ids:
        return {}
    return {
        node.id: node.title
        for node in session.scalars(
            select(KnowledgeNode).where(KnowledgeNode.id.in_(knowledge_node_ids))
        ).all()
    }

def submit_assessment(
    session: Session,
    *,
    assessment_id: str,
    user_id: str,
    request_id: str,
    answers: dict[str, str],
    submission_id: str,
) -> dict:
    assessment = session.get(Assessment, assessment_id)
    if assessment is None or assessment.user_id != user_id:
        raise LookupError(f"assessment {assessment_id} not found")
    draft = SQLAlchemyAssessmentRepository(
        session,
        user_id,
        assessment.goal_id,
    ).get_assessment_draft(assessment_id)
    validated_answers = validate_submitted_answers(draft, answers)
    payload_hash = canonical_json_hash({"answers": validated_answers})
    existing = session.scalar(
        select(AssessmentAttempt).where(
            AssessmentAttempt.assessment_id == assessment_id,
            AssessmentAttempt.user_id == user_id,
            AssessmentAttempt.submission_id == submission_id,
        )
    )
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise AssessmentConflict("The request ID was already used with a different answer payload.")
        return _public_submission_payload(session, _attempt_payload(session, existing))
    request = _resolve_tutor_request_thread(
        session,
        TutorRunRequest(
            trigger_type="assessment_submitted",
            user_id=user_id,
            goal_id=assessment.goal_id,
            thread_id="assessment-submit",
            assessment_id=assessment_id,
            submitted_answers=validated_answers,
            metadata={
                "submission_id": submission_id,
                "payload_hash": payload_hash,
            },
        ),
    )
    claimed = session.execute(
        update(Assessment)
        .where(
            Assessment.id == assessment_id,
            Assessment.user_id == user_id,
            Assessment.status == "active",
        )
        .values(status="submitted")
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        session.rollback()
        raise AssessmentConflict(f"assessment {assessment_id} was already submitted")
    assessment.status = "submitted"
    result = _run_engine(
        session,
        request,
    )
    refresh_phase_state_after_submit(session, assessment=assessment, result=result)
    if result.assessment_result is not None:
        _record_learning_event(
            session,
            user_id=user_id,
            goal_id=assessment.goal_id,
            task_id=None,
            session_id=None,
            event_type="assessment_submitted",
            source="assessment",
            event_payload={
                "assessment_id": assessment_id,
                "score": result.assessment_result.score,
                "mastery_updates": [item.model_dump() for item in result.mastery_updates],
            },
        )
        _refresh_activity_state(session, user_id=user_id, goal_id=assessment.goal_id)
    if result.assessment_result is None:
        raise RuntimeError("phase2 engine did not return an assessment result")
    payload = result.assessment_result.model_dump()
    payload.pop("submission_id", None)
    payload.pop("payload_hash", None)
    payload["mastery_updates"] = [item.model_dump() for item in result.mastery_updates]
    payload["observer_decision"] = result.observer_decision.model_dump() if result.observer_decision else None
    attempt = session.get(AssessmentAttempt, result.assessment_result.attempt_id)
    if attempt is not None:
        attempt.result_json = payload
    session.commit()
    return _public_submission_payload(session, payload)


def _attempt_payload(session: Session, attempt: AssessmentAttempt) -> dict:
    if attempt.result_json:
        return dict(attempt.result_json)
    answers = list(
        session.scalars(
            select(AssessmentAnswer).where(AssessmentAnswer.attempt_id == attempt.id).order_by(AssessmentAnswer.id)
        )
    )
    return {
        "assessment_id": attempt.assessment_id,
        "attempt_id": attempt.id,
        "score": attempt.score,
        "feedback": attempt.feedback,
        "status": attempt.status,
        "answers": [
            {
                "item_id": answer.item_id,
                "answer_text": answer.answer_text,
                "score": answer.score,
                "grader_type": answer.grader_type,
                "grader_reason": answer.grader_reason,
                "evidence_json": answer.evidence_json,
            }
            for answer in answers
        ],
        "mastery_updates": [],
        "observer_decision": None,
    }


def _public_submission_payload(session: Session, payload: dict) -> dict:
    answers = payload.get("answers", [])
    wrong_reason_tags = sorted(
        {
            tag
            for answer in answers
            for tag in (answer.get("evidence_json", {}) or {}).get("wrong_reason_tags", [])
            if isinstance(tag, str)
        }
    )
    mastery_updates = payload.get("mastery_updates", [])
    node_ids = {
        update.get("knowledge_node_id")
        for update in mastery_updates
        if isinstance(update, dict) and isinstance(update.get("knowledge_node_id"), str)
    }
    labels = {
        node.id: node.title
        for node in session.scalars(select(KnowledgeNode).where(KnowledgeNode.id.in_(node_ids))).all()
    } if node_ids else {}
    confidence = min(
        (float(update.get("confidence", 0)) for update in mastery_updates),
        default=0.0,
    )
    observer = payload.get("observer_decision") or {}
    decision = observer.get("decision", "manual_review")
    if decision not in {"keep", "reduce", "remediate", "advance", "manual_review"}:
        decision = "manual_review"
    return AssessmentSubmissionPublicResponse(
        assessment_id=payload["assessment_id"],
        attempt_id=payload["attempt_id"],
        status="graded" if payload.get("status") == "graded" else "review_required",
        score=payload.get("score"),
        feedback=str(payload.get("feedback", "")),
        grading=PublicGradingMetadata(
            mode="deterministic_fallback",
            grader_version="phase2-rubric-v1",
            confidence=confidence,
            needs_review=payload.get("status") != "graded",
            automatic_mastery_eligible=False,
        ),
        answers=[
            AssessmentAnswerPublicResult(
                item_id=answer["item_id"],
                score=answer.get("score"),
                feedback=str(answer.get("grader_reason", "")),
                wrong_reason_tags=list((answer.get("evidence_json", {}) or {}).get("wrong_reason_tags", [])),
                confidence=answer.get("confidence"),
                needs_review=False,
            )
            for answer in answers
        ],
        mastery_updates=[
            MasteryUpdatePublic(
                label=labels.get(update.get("knowledge_node_id"), "Knowledge progress"),
                previous_score=update["previous_score"],
                new_score=update["new_score"],
                new_confidence=update.get("confidence", 0),
                automatic_adjustment_eligible=False,
                reason_codes=wrong_reason_tags,
            )
            for update in mastery_updates
        ],
        observer_decision=ObserverDecisionPublic(
            policy_version="phase2-observer-v1",
            decision=decision,
            automation_allowed=False,
            confidence=confidence,
            reason_codes=wrong_reason_tags,
            user_facing_rationale=str(observer.get("rationale", "")),
        ),
        plan_adjustment=None,
    ).model_dump()


def validate_submitted_answers(
    draft: AssessmentDraft,
    answers: dict[str, str],
) -> dict[str, str]:
    known_item_ids = {item.item_id for item in draft.items}
    if set(answers) - known_item_ids:
        raise AssessmentAnswerValidationError(
            "assessment.unknown_item_id",
            "Submitted answers contain unknown assessment item IDs.",
        )
    return dict(answers)
