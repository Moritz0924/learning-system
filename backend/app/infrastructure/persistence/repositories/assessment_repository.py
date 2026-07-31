from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import (
    AssessmentAttemptResult,
    AssessmentDraft,
    AssessmentItem,
    MasteryUpdate,
    TutorRunResult,
)
from backend.app.models import (
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentItem as AssessmentItemModel,
    LearningStateSnapshot,
    MasteryRecord,
    PhaseAssessmentState,
)


@dataclass
class SQLAlchemyAssessmentRepository:
    session: Session
    user_id: str
    goal_id: str

    def save_assessment_draft(self, draft: AssessmentDraft) -> AssessmentDraft:
        if self.session.get(Assessment, draft.assessment_id) is not None:
            return draft
        snapshot = _load_snapshot(self.session, user_id=self.user_id, goal_id=self.goal_id)
        assessment = Assessment(
            id=draft.assessment_id,
            user_id=self.user_id,
            goal_id=self.goal_id,
            plan_id=snapshot.active_plan_id if snapshot else None,
            assessment_type=draft.assessment_type,
            scope=draft.scope,
            status="active",
            rubric_version="phase2-rubric-v1",
        )
        self.session.add(assessment)
        for item in draft.items:
            self.session.add(
                AssessmentItemModel(
                    id=item.item_id,
                    assessment_id=draft.assessment_id,
                    knowledge_node_id=item.knowledge_node_id,
                    question_type=item.question_type,
                    prompt=item.prompt,
                    options_json=item.options_json,
                    reference_answer=item.reference_answer,
                    rubric_json=item.rubric_json,
                    difficulty=item.difficulty,
                    source_chunk_ids=item.source_chunk_ids,
                )
            )
        self.session.flush()
        return draft.model_copy(update={"status": "active"})

    def get_assessment_draft(self, assessment_id: str) -> AssessmentDraft:
        assessment = self.session.get(Assessment, assessment_id)
        if assessment is None:
            raise LookupError(f"assessment {assessment_id} not found")
        items = list(
            self.session.scalars(
                select(AssessmentItemModel)
                .where(AssessmentItemModel.assessment_id == assessment_id)
                .order_by(AssessmentItemModel.id)
            )
        )
        return AssessmentDraft(
            assessment_id=assessment.id,
            assessment_type=assessment.assessment_type,
            status=assessment.status,
            scope=assessment.scope,
            items=[
                AssessmentItem(
                    item_id=item.id,
                    knowledge_node_id=item.knowledge_node_id,
                    question_type=item.question_type,
                    prompt=item.prompt,
                    options_json=item.options_json,
                    reference_answer=item.reference_answer,
                    rubric_json=item.rubric_json,
                    difficulty=item.difficulty,
                    source_chunk_ids=item.source_chunk_ids,
                )
                for item in items
            ],
        )

    def save_attempt_result(self, result: AssessmentAttemptResult) -> AssessmentAttemptResult:
        assessment = self.session.get(Assessment, result.assessment_id)
        if assessment is None:
            raise LookupError(f"assessment {result.assessment_id} not found")
        attempt = AssessmentAttempt(
            id=result.attempt_id,
            submission_id=result.submission_id or result.attempt_id,
            payload_hash=result.payload_hash or "",
            assessment_id=result.assessment_id,
            user_id=self.user_id,
            score=result.score,
            feedback=result.feedback,
            status=result.status,
        )
        self.session.add(attempt)
        for answer in result.answers:
            self.session.add(
                AssessmentAnswer(
                    id=f"answer-{uuid4()}",
                    attempt_id=attempt.id,
                    item_id=answer.item_id,
                    answer_text=answer.answer_text,
                    answer_json={"raw": answer.answer_text},
                    score=answer.score,
                    grader_type=answer.grader_type,
                    grader_reason=answer.grader_reason,
                    evidence_json={**answer.evidence_json, "confidence": answer.confidence},
                )
            )
        assessment.status = result.status
        assessment.total_score = result.score
        self.session.flush()
        return result

    def save_mastery_updates(self, updates: list[MasteryUpdate]) -> list[MasteryUpdate]:
        for update in updates:
            record = self.session.scalar(
                select(MasteryRecord).where(
                    MasteryRecord.user_id == self.user_id,
                    MasteryRecord.goal_id == self.goal_id,
                    MasteryRecord.knowledge_node_id == update.knowledge_node_id,
                )
            )
            if record is None:
                record = MasteryRecord(
                    id=f"mastery-{uuid4()}",
                    user_id=self.user_id,
                    goal_id=self.goal_id,
                    knowledge_node_id=update.knowledge_node_id,
                    mastery_score=update.new_score,
                    confidence=update.confidence,
                    evidence_count=update.evidence_count,
                    source_breakdown=update.source_breakdown,
                )
                self.session.add(record)
            else:
                record.mastery_score = update.new_score
                record.confidence = update.confidence
                record.evidence_count += update.evidence_count
                record.source_breakdown = update.source_breakdown
        _refresh_snapshot_mastery(self.session, user_id=self.user_id, goal_id=self.goal_id, updates=updates)
        self.session.flush()
        return updates

def _load_snapshot(session: Session, *, user_id: str, goal_id: str) -> LearningStateSnapshot | None:
    return session.scalar(
        select(LearningStateSnapshot).where(
            LearningStateSnapshot.user_id == user_id,
            LearningStateSnapshot.goal_id == goal_id,
        )
    )

def _refresh_snapshot_mastery(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    updates: list[MasteryUpdate],
) -> None:
    snapshot = _load_snapshot(session, user_id=user_id, goal_id=goal_id)
    if snapshot is None:
        return
    mastery = dict(snapshot.mastery_summary or {})
    for update in updates:
        mastery[update.knowledge_node_id] = {
            "knowledge_node_id": update.knowledge_node_id,
            "score": update.new_score,
            "confidence": update.confidence,
        }
    state = dict(snapshot.current_state or {})
    state["review_queue"] = [
        {"knowledge_node_id": update.knowledge_node_id, "reason": "assessment_score_below_threshold"}
        for update in updates
        if update.new_score < 70
    ]
    snapshot.mastery_summary = mastery
    snapshot.current_state = state

def upsert_phase_state(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    assessment_id: str,
    phase_code: str,
    knowledge_node_ids: list[str],
    status: str,
) -> PhaseAssessmentState:
    phase_state = session.scalar(
        select(PhaseAssessmentState).where(
            PhaseAssessmentState.user_id == user_id,
            PhaseAssessmentState.goal_id == goal_id,
            PhaseAssessmentState.phase_code == phase_code,
        )
    )
    if phase_state is None:
        phase_state = PhaseAssessmentState(
            id=f"phase-state-{uuid4()}",
            user_id=user_id,
            goal_id=goal_id,
            assessment_id=assessment_id,
            phase_code=phase_code,
            covered_node_ids=knowledge_node_ids,
            status=status,
            readiness_score=0,
            last_result_json={},
            next_action="review",
        )
        session.add(phase_state)
    else:
        phase_state.assessment_id = assessment_id
        phase_state.covered_node_ids = knowledge_node_ids
        phase_state.status = status
    session.flush()
    return phase_state

def refresh_phase_state_after_submit(
    session: Session,
    *,
    assessment: Assessment,
    result: TutorRunResult,
) -> None:
    if assessment.assessment_type != "phase" or result.assessment_result is None:
        return
    phase_state = session.scalar(
        select(PhaseAssessmentState).where(PhaseAssessmentState.assessment_id == assessment.id)
    )
    if phase_state is None:
        return
    phase_state.status = "graded"
    phase_state.readiness_score = result.assessment_result.score
    phase_state.last_result_json = result.assessment_result.model_dump()
    phase_state.next_action = "advance" if result.assessment_result.score >= 70 else "review"
