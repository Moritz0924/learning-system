from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from math import sqrt
from pathlib import Path
import sys
from uuid import uuid4

from pypdf import PdfReader
from sqlalchemy import case, delete, or_, select
from sqlalchemy.orm import Session

_SRC_PATH = Path(__file__).resolve().parents[3] / "src"
if _SRC_PATH.exists() and str(_SRC_PATH) not in sys.path:
    sys.path.append(str(_SRC_PATH))

from adaptive_tutor.phase2.assessment import build_assessment_draft
from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.ports import Phase2Dependencies
from adaptive_tutor.phase2.rag import split_text
from adaptive_tutor.phase2.replanning import build_observer_signals
from adaptive_tutor.phase2.schemas import (
    AssessmentAttemptResult,
    AssessmentDraft,
    AssessmentItem,
    MasteryUpdate,
    PlanAdjustment,
    RetrievedChunk,
    TutorRunRequest,
    TutorRunResult,
)
from backend.app.models import (
    AgentRun,
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentItem as AssessmentItemModel,
    Document,
    DocumentChunk,
    KnowledgeNode,
    LearningEvent,
    LearningPlan,
    LearningSession,
    LearningStateSnapshot,
    MasteryRecord,
    PhaseAssessmentState,
    PlanAdjustmentRecord,
    PlanTask,
    ToolCall,
)
from backend.app.services.llm_gateway import LLMGatewayClient


class PlanApplicationConflict(ValueError):
    pass


class DeterministicEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        digest = sha256(text.lower().encode("utf-8")).digest()
        return [byte / 255 for byte in digest[:16]]


class NoopOCRClient:
    def extract_text(self, content: bytes, *, filename: str) -> str:
        return f"OCR text extracted from {filename} ({len(content)} bytes)."


@dataclass
class SQLAlchemyStateRepository:
    session: Session

    def load_context(self, user_id: str, goal_id: str) -> dict:
        snapshot = self._snapshot(user_id, goal_id)
        task_query = select(PlanTask).where(PlanTask.user_id == user_id, PlanTask.goal_id == goal_id)
        if snapshot and snapshot.active_plan_id:
            task_query = task_query.where(PlanTask.plan_id == snapshot.active_plan_id)
        task = self.session.scalar(
            task_query.order_by(
                case((PlanTask.status == "active", 0), else_=1),
                PlanTask.scheduled_day,
                PlanTask.id,
            )
        )
        if snapshot is None:
            return {
                "user_id": user_id,
                "goal_id": goal_id,
                "active_plan": {"id": "plan-unknown", "version": 0},
                "current_task": {"knowledge_node_ids": [task.knowledge_node_id]} if task else None,
                "mastery_summary": {},
                "recent_learning_events": _recent_learning_events(self.session, user_id=user_id, goal_id=goal_id),
                "completion_rate_7d": self._completion_rate_7d(user_id, goal_id),
                "observer_signals": self._observer_signals(user_id, goal_id, None),
            }
        mastery_summary = self._mastery_by_node_id(user_id, goal_id, snapshot)
        return {
            "user_id": user_id,
            "goal_id": goal_id,
            "active_plan": {"id": snapshot.active_plan_id, "version": snapshot.active_plan_version},
            "current_task": {"knowledge_node_ids": [task.knowledge_node_id]} if task else None,
            "mastery_summary": mastery_summary,
            "recent_learning_events": _recent_learning_events(self.session, user_id=user_id, goal_id=goal_id),
            "completion_rate_7d": self._completion_rate_7d(user_id, goal_id),
            "current_state": snapshot.current_state or {},
            "observer_signals": self._observer_signals(user_id, goal_id, snapshot),
        }

    def refresh_snapshot(self, user_id: str, goal_id: str, updates: dict) -> dict:
        snapshot = self._snapshot(user_id, goal_id)
        if snapshot is None:
            return updates
        current_state = dict(snapshot.current_state or {})
        if "latest_plan_adjustment" in updates:
            current_state["latest_plan_adjustment"] = updates["latest_plan_adjustment"]
        if "review_queue" in updates:
            current_state["review_queue"] = updates["review_queue"]
        if "current_state" in updates:
            current_state.update(updates["current_state"])
        if "mastery_summary" in updates:
            snapshot.mastery_summary = updates["mastery_summary"]
        if "latest_plan_adjustment_id" in updates:
            snapshot.latest_plan_adjustment_id = updates["latest_plan_adjustment_id"]
        if "phase_assessment_state_id" in updates:
            snapshot.phase_assessment_state_id = updates["phase_assessment_state_id"]
        snapshot.current_state = current_state
        generated_from = dict(snapshot.generated_from or {})
        generated_from.update(updates.get("generated_from", {}))
        snapshot.generated_from = generated_from
        self.session.flush()
        return self.load_context(user_id, goal_id)

    def _snapshot(self, user_id: str, goal_id: str) -> LearningStateSnapshot | None:
        return self.session.scalar(
            select(LearningStateSnapshot).where(
                LearningStateSnapshot.user_id == user_id,
                LearningStateSnapshot.goal_id == goal_id,
            )
        )

    def _mastery_by_node_id(self, user_id: str, goal_id: str, snapshot: LearningStateSnapshot) -> dict:
        records = self.session.scalars(
            select(MasteryRecord).where(
                MasteryRecord.user_id == user_id,
                MasteryRecord.goal_id == goal_id,
            )
        ).all()
        if records:
            return {
                record.knowledge_node_id: {
                    "score": record.mastery_score,
                    "confidence": record.confidence,
                    "evidence_count": record.evidence_count,
                }
                for record in records
            }
        return snapshot.mastery_summary or {}

    def _observer_signals(
        self,
        user_id: str,
        goal_id: str,
        snapshot: LearningStateSnapshot | None,
    ) -> dict:
        completion_rate = self._completion_rate_7d(user_id, goal_id)
        correctness_rate, recent_attempts, wrong_reason_tags = self._recent_assessment_signals(user_id, goal_id)
        mastery_delta, low_mastery_nodes = self._mastery_signals(user_id, goal_id)
        current_state = dict(snapshot.current_state or {}) if snapshot else {}
        return build_observer_signals(
            completion_rate_7d=completion_rate,
            correctness_rate=correctness_rate,
            mastery_delta=mastery_delta,
            low_mastery_nodes=low_mastery_nodes,
            wrong_reason_tags=wrong_reason_tags,
            recent_attempts=recent_attempts,
            review_queue=current_state.get("review_queue", []),
            phase_assessment=self._phase_assessment_signal(user_id, goal_id, snapshot),
        )

    def _completion_rate_7d(self, user_id: str, goal_id: str) -> float | None:
        tasks = self.session.scalars(
            select(PlanTask).where(
                PlanTask.user_id == user_id,
                PlanTask.goal_id == goal_id,
                PlanTask.scheduled_day <= 7,
            )
        ).all()
        observed_statuses = {"completed", "done", "missed", "skipped", "incomplete", "failed"}
        completed_statuses = {"completed", "done"}
        observed_tasks = [task for task in tasks if (task.status or "").lower() in observed_statuses]
        if not observed_tasks:
            return None
        completed = sum(1 for task in observed_tasks if (task.status or "").lower() in completed_statuses)
        return completed / len(observed_tasks)

    def _recent_assessment_signals(self, user_id: str, goal_id: str) -> tuple[float | None, list[dict], list[str]]:
        rows = self.session.execute(
            select(AssessmentAttempt, Assessment)
            .join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
            .where(
                Assessment.user_id == user_id,
                Assessment.goal_id == goal_id,
                AssessmentAttempt.user_id == user_id,
                AssessmentAttempt.status == "graded",
            )
            .order_by(AssessmentAttempt.submitted_at.desc())
            .limit(3)
        ).all()
        if not rows:
            return None, [], []

        attempts = [row[0] for row in rows]
        assessments = [row[1] for row in rows]
        correctness_rate = sum(attempt.score for attempt in attempts) / (100 * len(attempts))
        recent_attempts = [
            {
                "assessment_id": assessment.id,
                "attempt_id": attempt.id,
                "assessment_type": assessment.assessment_type,
                "score": attempt.score,
                "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
            }
            for attempt, assessment in zip(attempts, assessments)
        ]
        answers = self.session.scalars(
            select(AssessmentAnswer).where(AssessmentAnswer.attempt_id.in_([attempt.id for attempt in attempts]))
        ).all()
        wrong_reason_tags = [
            tag
            for answer in answers
            for tag in (answer.evidence_json or {}).get("wrong_reason_tags", [])
        ]
        return correctness_rate, recent_attempts, wrong_reason_tags

    def _mastery_signals(self, user_id: str, goal_id: str) -> tuple[float | None, list[dict]]:
        records = self.session.scalars(
            select(MasteryRecord).where(
                MasteryRecord.user_id == user_id,
                MasteryRecord.goal_id == goal_id,
            )
        ).all()
        if not records:
            return None, []

        deltas: list[float] = []
        low_mastery_nodes = []
        for record in records:
            source = record.source_breakdown or {}
            historical = source.get("historical_mastery")
            if historical is None:
                historical = source.get("baseline")
            if isinstance(historical, (int, float)):
                deltas.append(record.mastery_score - float(historical))
            if record.mastery_score < 70:
                low_mastery_nodes.append(
                    {
                        "knowledge_node_id": record.knowledge_node_id,
                        "score": record.mastery_score,
                        "confidence": record.confidence,
                    }
                )
        return (min(deltas) if deltas else None), low_mastery_nodes

    def _phase_assessment_signal(
        self,
        user_id: str,
        goal_id: str,
        snapshot: LearningStateSnapshot | None,
    ) -> dict | None:
        phase_state = None
        if snapshot and snapshot.phase_assessment_state_id:
            phase_state = self.session.get(PhaseAssessmentState, snapshot.phase_assessment_state_id)
        if phase_state is None:
            phase_state = self.session.scalar(
                select(PhaseAssessmentState)
                .where(PhaseAssessmentState.user_id == user_id, PhaseAssessmentState.goal_id == goal_id)
                .order_by(PhaseAssessmentState.updated_at.desc())
            )
        if phase_state is None:
            return None
        return {
            "phase_assessment_state_id": phase_state.id,
            "phase_code": phase_state.phase_code,
            "status": phase_state.status,
            "readiness_score": phase_state.readiness_score,
            "next_action": phase_state.next_action,
        }


@dataclass
class SQLAlchemyRagRepository:
    session: Session
    embedding_client: DeterministicEmbeddingClient

    def retrieve(self, query: str, *, top_k: int = 5, user_id: str | None = None) -> list[RetrievedChunk]:
        visibility_filter = (
            or_(Document.corpus_type == "curated", Document.owner_user_id == user_id)
            if user_id
            else Document.corpus_type == "curated"
        )
        rows = self.session.execute(
            select(DocumentChunk, Document).join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.parse_status == "success")
            .where(visibility_filter)
        ).all()
        if not rows:
            return _default_citations(query)
        query_embedding = self.embedding_client.embed(query)
        ranked = sorted(
            rows,
            key=lambda row: _cosine_similarity(query_embedding, row[0].embedding or []),
            reverse=True,
        )
        return [
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=document.id,
                content=chunk.content,
                citation_label=chunk.citation_label,
                source_title=document.filename,
                source_url=document.source_url,
                trusted_level=document.trusted_level,
                metadata={
                    **(chunk.metadata_json or {}),
                    "untrusted_input": document.corpus_type != "curated",
                    "corpus_type": document.corpus_type,
                },
            )
            for chunk, document in ranked[:top_k]
        ]


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
                    evidence_json=answer.evidence_json,
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


@dataclass
class SQLAlchemyPlanRepository:
    session: Session

    def save_plan_adjustment(self, adjustment: PlanAdjustment) -> PlanAdjustment:
        record_id = adjustment.adjustment_id or f"adjustment-{uuid4()}"
        record = PlanAdjustmentRecord(
            id=record_id,
            user_id=adjustment.user_id or "",
            goal_id=adjustment.goal_id or "",
            previous_plan_id=adjustment.previous_plan_id,
            new_plan_id=adjustment.new_plan_id,
            trigger_type=adjustment.trigger_type,
            decision=adjustment.decision,
            evidence_json=adjustment.evidence_json,
            before_snapshot=adjustment.before_snapshot,
            after_snapshot=adjustment.after_snapshot,
            plan_patch=adjustment.plan_patch,
            change_summary=adjustment.change_summary,
            rationale_json=adjustment.rationale_json,
            status=adjustment.status,
        )
        self.session.add(record)
        self.session.flush()
        return adjustment.model_copy(update={"adjustment_id": record.id})


@dataclass
class SQLAlchemyAuditSink:
    session: Session
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    last_agent_run_id: str | None = None

    def record_agent_run(self, payload: dict) -> None:
        record = AgentRun(
            id=f"run-{uuid4()}",
            user_id=payload["user_id"],
            thread_id=payload["thread_id"],
            graph_name=payload["graph_name"],
            graph_version=payload["graph_version"],
            trigger_type=payload["trigger_type"],
            input_snapshot=payload,
            output_snapshot={"status": payload["status"]},
            status=payload["status"],
            latency_ms=payload["latency_ms"],
            error_message=payload.get("error_message"),
        )
        self.session.add(record)
        self.last_agent_run_id = record.id
        for tool_call in self.pending_tool_calls:
            tool_call.agent_run_id = record.id
        self.session.flush()

    def record_tool_call(self, payload: dict) -> None:
        record = ToolCall(
            id=f"tool-{uuid4()}",
            agent_run_id=self.last_agent_run_id,
            tool_name=payload["tool_name"],
            request_hash=sha256(str(payload.get("request_hash", "")).encode("utf-8")).hexdigest(),
            response_summary=payload.get("response_summary", {}),
            source_urls=payload.get("source_urls", []),
            status=payload["status"],
        )
        self.session.add(record)
        if record.agent_run_id is None:
            self.pending_tool_calls.append(record)
        self.session.flush()


def answer_tutor_question(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    thread_id: str,
    message: str,
) -> dict:
    result = _run_engine(
        session,
        TutorRunRequest(
            trigger_type="chat",
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            user_message=message,
        ),
    )
    session.commit()
    return _run_result_to_dict(result)


def create_assessment(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    assessment_type: str,
    knowledge_node_ids: list[str],
) -> dict:
    result = _run_engine(
        session,
        TutorRunRequest(
            trigger_type="assessment_due",
            user_id=user_id,
            goal_id=goal_id,
            thread_id="assessment",
            assessment_type=assessment_type,
            knowledge_node_ids=knowledge_node_ids,
        ),
    )
    session.commit()
    if result.assessment_draft is None:
        raise RuntimeError("phase2 engine did not return an assessment draft")
    return _draft_to_dict(result.assessment_draft)


def create_phase_assessment(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    thread_id: str,
    phase_code: str,
    knowledge_node_ids: list[str],
) -> dict:
    result = _run_engine(
        session,
        TutorRunRequest(
            trigger_type="assessment_due",
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            assessment_type="phase",
            knowledge_node_ids=knowledge_node_ids,
        ),
    )
    if result.assessment_draft is None:
        raise RuntimeError("phase2 engine did not return a phase assessment draft")
    phase_state = _upsert_phase_state(
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
    payload = _draft_to_dict(result.assessment_draft)
    payload["phase_assessment_state_id"] = phase_state.id
    payload["phase_code"] = phase_code
    return payload


def submit_assessment(
    session: Session,
    *,
    assessment_id: str,
    user_id: str,
    answers: dict[str, str],
) -> dict:
    assessment = session.get(Assessment, assessment_id)
    if assessment is None:
        raise LookupError(f"assessment {assessment_id} not found")
    result = _run_engine(
        session,
        TutorRunRequest(
            trigger_type="assessment_submitted",
            user_id=user_id,
            goal_id=assessment.goal_id,
            thread_id="assessment-submit",
            assessment_id=assessment_id,
            submitted_answers=answers,
        ),
    )
    _refresh_phase_state_after_submit(session, assessment=assessment, result=result)
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
    session.commit()
    if result.assessment_result is None:
        raise RuntimeError("phase2 engine did not return an assessment result")
    payload = result.assessment_result.model_dump()
    payload["mastery_updates"] = [item.model_dump() for item in result.mastery_updates]
    payload["observer_decision"] = result.observer_decision.model_dump() if result.observer_decision else None
    return payload


def request_replan(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    message: str,
) -> dict:
    result = _run_engine(
        session,
        TutorRunRequest(
            trigger_type="manual_replan",
            user_id=user_id,
            goal_id=goal_id,
            thread_id="manual-replan",
            user_message=message,
        ),
    )
    session.commit()
    if result.plan_adjustment is None:
        raise RuntimeError("phase2 engine did not return a plan adjustment")
    return _plan_adjustment_model_to_dict(result.plan_adjustment)


def start_task(
    session: Session,
    *,
    user_id: str,
    task_id: str,
) -> dict:
    task = _load_task_for_user(session, user_id=user_id, task_id=task_id)
    active_session = session.scalar(
        select(LearningSession).where(
            LearningSession.user_id == user_id,
            LearningSession.task_id == task_id,
            LearningSession.status == "active",
        )
    )
    if active_session is None:
        active_session = LearningSession(
            id=f"session-{uuid4()}",
            user_id=user_id,
            goal_id=task.goal_id,
            plan_id=task.plan_id,
            task_id=task.id,
            started_at=datetime.utcnow(),
            duration_minutes=0,
            status="active",
            evidence_json={},
        )
        session.add(active_session)
    if task.status not in {"completed", "done"}:
        task.status = "active"
    session.flush()
    _record_learning_event(
        session,
        user_id=user_id,
        goal_id=task.goal_id,
        task_id=task.id,
        session_id=active_session.id,
        event_type="task_started",
        source="task_api",
        event_payload={"plan_id": task.plan_id, "task_title": task.title},
    )
    _refresh_activity_state(session, user_id=user_id, goal_id=task.goal_id)
    session.commit()
    return {"task": _task_to_dict(task), "session": _learning_session_to_dict(active_session)}


def complete_task(
    session: Session,
    *,
    user_id: str,
    task_id: str,
    duration_minutes: int | None,
    evidence: dict,
) -> dict:
    task = _load_task_for_user(session, user_id=user_id, task_id=task_id)
    active_session = session.scalar(
        select(LearningSession).where(
            LearningSession.user_id == user_id,
            LearningSession.task_id == task_id,
            LearningSession.status == "active",
        )
    )
    if active_session is None:
        active_session = LearningSession(
            id=f"session-{uuid4()}",
            user_id=user_id,
            goal_id=task.goal_id,
            plan_id=task.plan_id,
            task_id=task.id,
            started_at=datetime.utcnow(),
            duration_minutes=0,
            status="active",
            evidence_json={},
        )
        session.add(active_session)
        session.flush()

    ended_at = datetime.utcnow()
    active_session.ended_at = ended_at
    active_session.duration_minutes = duration_minutes or _elapsed_minutes(active_session.started_at, ended_at)
    active_session.status = "completed"
    active_session.evidence_json = evidence
    task.status = "completed"
    _record_learning_event(
        session,
        user_id=user_id,
        goal_id=task.goal_id,
        task_id=task.id,
        session_id=active_session.id,
        event_type="task_completed",
        source="task_api",
        event_payload={
            "plan_id": task.plan_id,
            "task_title": task.title,
            "duration_minutes": active_session.duration_minutes,
            "evidence": evidence,
        },
    )
    _refresh_activity_state(session, user_id=user_id, goal_id=task.goal_id)
    result = _run_engine(
        session,
        TutorRunRequest(
            trigger_type="task_completed",
            user_id=user_id,
            goal_id=task.goal_id,
            thread_id=f"task-{task.id}",
            metadata={"task_id": task.id},
        ),
    )
    session.commit()
    return {
        "task": _task_to_dict(task),
        "session": _learning_session_to_dict(active_session),
        "observer_decision": result.observer_decision.model_dump() if result.observer_decision else None,
        "plan_adjustment": _plan_adjustment_model_to_dict(result.plan_adjustment) if result.plan_adjustment else None,
    }


def apply_plan_adjustment(
    session: Session,
    *,
    adjustment_id: str,
    user_id: str,
    goal_id: str,
) -> dict:
    record = session.get(PlanAdjustmentRecord, adjustment_id)
    if record is None or record.user_id != user_id or record.goal_id != goal_id:
        raise LookupError(f"plan adjustment {adjustment_id} not found")
    if record.status != "proposed":
        raise PlanApplicationConflict(f"plan adjustment {adjustment_id} is not proposed")
    patch = _json_dict(record.plan_patch)
    if record.decision == "keep" or patch.get("no_change"):
        raise PlanApplicationConflict("no applicable plan patch for keep adjustment")

    snapshot = _load_snapshot(session, user_id=user_id, goal_id=goal_id)
    previous_plan_id = record.previous_plan_id or (snapshot.active_plan_id if snapshot else None)
    previous_plan = session.get(LearningPlan, previous_plan_id) if previous_plan_id else None
    if previous_plan is None:
        raise LookupError("previous learning plan not found")

    created_tasks = _create_applied_plan_tasks(
        session,
        previous_plan=previous_plan,
        adjustment=record,
        snapshot=snapshot,
    )
    new_plan = created_tasks["plan"]
    task_payloads = created_tasks["tasks"]

    previous_plan.status = "replaced"
    record.status = "applied"
    record.new_plan_id = new_plan.id
    record.after_snapshot = {
        **_json_dict(record.after_snapshot),
        "active_plan": {"id": new_plan.id, "version": new_plan.version},
        "created_task_ids": [task["id"] for task in task_payloads],
    }
    if snapshot is not None:
        snapshot.active_plan_id = new_plan.id
        snapshot.active_plan_version = new_plan.version
        snapshot.latest_plan_adjustment_id = record.id
        current_state = dict(snapshot.current_state or {})
        current_state["latest_plan_adjustment"] = _plan_adjustment_record_to_dict(record)
        snapshot.current_state = current_state
        generated_from = dict(snapshot.generated_from or {})
        generated_from["latest_plan_adjustment_id"] = record.id
        generated_from["active_plan_id"] = new_plan.id
        snapshot.generated_from = generated_from
    _record_learning_event(
        session,
        user_id=user_id,
        goal_id=goal_id,
        task_id=None,
        session_id=None,
        event_type="plan_adjustment_applied",
        source="plans_api",
        event_payload={
            "adjustment_id": record.id,
            "previous_plan_id": previous_plan.id,
            "new_plan_id": new_plan.id,
            "decision": record.decision,
        },
    )
    _refresh_activity_state(session, user_id=user_id, goal_id=goal_id)
    session.commit()
    payload = _plan_adjustment_record_to_dict(record)
    payload["active_plan"] = {"id": new_plan.id, "version": new_plan.version}
    payload["created_tasks"] = task_payloads
    return payload


def create_document_record(
    session: Session,
    *,
    user_id: str,
    filename: str,
    mime_type: str,
    content: str = "",
    content_bytes: bytes | None = None,
    source_url: str | None = None,
    processing_mode: str | None = None,
) -> dict:
    payload = content_bytes if content_bytes is not None else content.encode("utf-8")
    if not payload:
        raise ValueError("document upload content is required")
    digest = sha256(payload).hexdigest()
    document = Document(
        id=f"doc-{uuid4()}",
        owner_user_id=user_id,
        corpus_type="user_uploaded",
        filename=filename,
        object_key=f"uploads/{user_id}/{digest[:12]}-{filename}",
        mime_type=mime_type,
        parse_status="pending",
        sha256=digest,
        source_url=source_url,
        trusted_level=1,
    )
    session.add(document)
    session.flush()
    mode = (processing_mode or os.getenv("DOCUMENT_PROCESSING_MODE", "inline")).lower()
    if mode == "defer":
        session.commit()
        return _document_to_dict(document)
    if mode == "celery":
        session.commit()
        from backend.app.worker import process_document_upload_task

        process_document_upload_task.delay(document.id, base64.b64encode(payload).decode("ascii"))
        return _document_to_dict(document)
    process_document_upload(session, document_id=document.id, content_bytes=payload)
    session.commit()
    return _document_to_dict(document)


def process_document_upload(
    session: Session,
    *,
    document_id: str,
    content_bytes: bytes,
) -> dict:
    document = session.get(Document, document_id)
    if document is None:
        raise LookupError(f"document {document_id} not found")
    document.parse_status = "processing"
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    session.flush()
    try:
        parsed_chunks = _parse_document_content(
            content_bytes,
            filename=document.filename,
            mime_type=document.mime_type,
        )
        _store_document_chunks(session, document=document, parsed_chunks=parsed_chunks)
        document.parse_status = "success"
        session.flush()
        return {"document_id": document.id, "status": "success", "chunk_count": len(parsed_chunks)}
    except ValueError:
        document.parse_status = "failed"
        session.flush()
        return {"document_id": document.id, "status": "failed", "chunk_count": 0}


def _parse_document_content(content_bytes: bytes, *, filename: str, mime_type: str) -> list[dict]:
    normalized_type = mime_type.lower()
    suffix = Path(filename).suffix.lower()
    if normalized_type in {"text/markdown", "text/plain", "application/markdown"} or suffix in {
        ".md",
        ".markdown",
    }:
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("markdown document must be utf-8 text") from exc
        normalized = _normalize_text(content)
        if not normalized:
            raise ValueError("document contains no text")
        return [
            {"content": chunk, "source_type": "markdown", "page_number": None}
            for chunk in split_text(normalized)
        ]
    if normalized_type == "application/pdf" or suffix == ".pdf":
        return _parse_pdf_content(content_bytes)
    raise ValueError(f"unsupported document mime type: {mime_type}")


def _parse_pdf_content(content_bytes: bytes) -> list[dict]:
    try:
        reader = PdfReader(BytesIO(content_bytes))
    except Exception as exc:
        raise ValueError("pdf document could not be parsed") from exc
    parsed: list[dict] = []
    for page_number, page in enumerate(reader.pages, start=1):
        normalized = _normalize_text(page.extract_text() or "")
        if not normalized:
            continue
        parsed.extend(
            {"content": chunk, "source_type": "pdf", "page_number": page_number}
            for chunk in split_text(normalized)
        )
    if not parsed:
        raise ValueError("pdf document contains no extractable text")
    return parsed


def _normalize_text(content: str) -> str:
    return "\n".join(line.strip("# ").strip() for line in content.splitlines() if line.strip())


def _store_document_chunks(session: Session, *, document: Document, parsed_chunks: list[dict]) -> None:
    embedding = DeterministicEmbeddingClient()
    for index, parsed in enumerate(parsed_chunks, start=1):
        chunk_content = parsed["content"]
        metadata = {"source_type": parsed["source_type"], "untrusted_input": True, "chunk_index": index}
        if parsed.get("page_number") is not None:
            metadata["page_number"] = parsed["page_number"]
        citation_label = (
            f"{document.filename} page {parsed['page_number']} chunk {index}"
            if parsed.get("page_number") is not None
            else f"{document.filename} chunk {index}"
        )
        session.add(
            DocumentChunk(
                id=f"chunk-{uuid4()}",
                document_id=document.id,
                chunk_index=index,
                content=chunk_content,
                token_count=len(chunk_content.split()),
                embedding=embedding.embed(chunk_content),
                metadata_json=metadata,
                citation_label=citation_label,
            )
        )


def list_document_records(session: Session, *, user_id: str) -> list[dict]:
    documents = session.scalars(select(Document).where(Document.owner_user_id == user_id)).all()
    return [_document_to_dict(document) for document in documents]


def load_learning_activity_summary(session: Session, *, user_id: str, goal_id: str) -> dict:
    return {
        "recent_learning_events": _recent_learning_events(session, user_id=user_id, goal_id=goal_id),
        "completion_rate_7d": _completion_rate_7d(session, user_id=user_id, goal_id=goal_id),
    }


def load_plan_adjustment(session: Session, adjustment_id: str | None) -> dict | None:
    if not adjustment_id:
        return None
    record = session.get(PlanAdjustmentRecord, adjustment_id)
    return _plan_adjustment_record_to_dict(record) if record else None


def _load_task_for_user(session: Session, *, user_id: str, task_id: str) -> PlanTask:
    task = session.get(PlanTask, task_id)
    if task is None or task.user_id != user_id:
        raise LookupError(f"task {task_id} not found")
    return task


def _elapsed_minutes(started_at: datetime, ended_at: datetime) -> int:
    return max(1, int((ended_at - started_at).total_seconds() // 60))


def _record_learning_event(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    session_id: str | None,
    task_id: str | None,
    event_type: str,
    source: str,
    event_payload: dict,
) -> LearningEvent:
    record = LearningEvent(
        id=f"event-{uuid4()}",
        user_id=user_id,
        goal_id=goal_id,
        session_id=session_id,
        task_id=task_id,
        event_type=event_type,
        source=source,
        event_payload=event_payload,
        occurred_at=datetime.utcnow(),
    )
    session.add(record)
    session.flush()
    return record


def _recent_learning_events(session: Session, *, user_id: str, goal_id: str, limit: int = 5) -> list[dict]:
    events = list(
        session.scalars(
            select(LearningEvent)
            .where(LearningEvent.user_id == user_id, LearningEvent.goal_id == goal_id)
            .order_by(LearningEvent.occurred_at.desc())
            .limit(limit)
        )
    )
    return [_learning_event_to_dict(event) for event in reversed(events)]


def _completion_rate_7d(session: Session, *, user_id: str, goal_id: str) -> float | None:
    tasks = session.scalars(
        select(PlanTask).where(
            PlanTask.user_id == user_id,
            PlanTask.goal_id == goal_id,
            PlanTask.scheduled_day <= 7,
        )
    ).all()
    observed_statuses = {"completed", "done", "missed", "skipped", "incomplete", "failed"}
    observed = [task for task in tasks if (task.status or "").lower() in observed_statuses]
    if not observed:
        return None
    completed = sum(1 for task in observed if (task.status or "").lower() in {"completed", "done"})
    return round(completed / len(observed), 4)


def _refresh_activity_state(session: Session, *, user_id: str, goal_id: str) -> None:
    snapshot = _load_snapshot(session, user_id=user_id, goal_id=goal_id)
    if snapshot is None:
        return
    state = dict(snapshot.current_state or {})
    state.update(load_learning_activity_summary(session, user_id=user_id, goal_id=goal_id))
    snapshot.current_state = state
    session.flush()


def _create_applied_plan_tasks(
    session: Session,
    *,
    previous_plan: LearningPlan,
    adjustment: PlanAdjustmentRecord,
    snapshot: LearningStateSnapshot | None,
) -> dict:
    patch = _json_dict(adjustment.plan_patch)
    previous_tasks = list(
        session.scalars(
            select(PlanTask)
            .where(PlanTask.plan_id == previous_plan.id)
            .order_by(PlanTask.scheduled_day, PlanTask.priority, PlanTask.id)
        )
    )
    open_tasks = [task for task in previous_tasks if (task.status or "").lower() not in {"completed", "done"}]
    new_plan = LearningPlan(
        id=f"plan-{uuid4()}",
        user_id=previous_plan.user_id,
        goal_id=previous_plan.goal_id,
        curriculum_id=previous_plan.curriculum_id,
        version=_next_plan_version(session, previous_plan.user_id, previous_plan.goal_id),
        status="active",
        generated_by="planner",
        rationale_json={
            "source": "plan_adjustment",
            "adjustment_id": adjustment.id,
            "decision": adjustment.decision,
        },
        valid_from=date.today(),
        valid_to=previous_plan.valid_to,
        plan_json={
            **(previous_plan.plan_json or {}),
            "applied_adjustment_id": adjustment.id,
            "previous_plan_id": previous_plan.id,
        },
    )
    session.add(new_plan)
    session.flush()

    created: list[PlanTask] = []
    day_offset = 0
    if adjustment.decision == "remediate":
        review_count = int(patch.get("review_task_count", 2))
        review_nodes = _review_nodes_for_adjustment(session, adjustment=adjustment, snapshot=snapshot, fallback_tasks=open_tasks)
        for index, node in enumerate(review_nodes[:review_count], start=1):
            created.append(
                _add_plan_task(
                    session,
                    plan=new_plan,
                    knowledge_node_id=node["id"],
                    knowledge_node_code=node["code"],
                    title=f"Review {node['code']}",
                    task_type="review",
                    objective="Review weak knowledge area before continuing.",
                    scheduled_day=index,
                    estimated_minutes=30,
                    priority=0,
                    payload={"source": "plan_adjustment", "adjustment_id": adjustment.id},
                )
            )
        day_offset = len(created)

    multiplier = float(patch.get("load_multiplier", 1.0)) if adjustment.decision == "reduce" else 1.0
    for task in open_tasks:
        created.append(
            _clone_plan_task(
                session,
                source=task,
                plan=new_plan,
                scheduled_day=task.scheduled_day + day_offset,
                estimated_minutes=max(10, int(round(task.estimated_minutes * multiplier))),
                adjustment_id=adjustment.id,
            )
        )

    if adjustment.decision == "advance":
        next_node = _next_uncovered_node(session, previous_plan=previous_plan, tasks=previous_tasks)
        if next_node is not None:
            created.append(
                _add_plan_task(
                    session,
                    plan=new_plan,
                    knowledge_node_id=next_node.id,
                    knowledge_node_code=next_node.code,
                    title=f"Practice {next_node.code}",
                    task_type="practice",
                    objective=f"Apply {next_node.code.replace('_', ' ')} in a short practice task.",
                    scheduled_day=(max([task.scheduled_day for task in created], default=0) + 1),
                    estimated_minutes=45,
                    priority=2,
                    payload={"source": "plan_adjustment", "adjustment_id": adjustment.id},
                )
            )

    session.flush()
    return {"plan": new_plan, "tasks": [_task_to_dict(task) for task in created]}


def _add_plan_task(
    session: Session,
    *,
    plan: LearningPlan,
    knowledge_node_id: str,
    knowledge_node_code: str,
    title: str,
    task_type: str,
    objective: str,
    scheduled_day: int,
    estimated_minutes: int,
    priority: int,
    payload: dict,
) -> PlanTask:
    task = PlanTask(
        id=f"task-{uuid4()}",
        plan_id=plan.id,
        user_id=plan.user_id,
        goal_id=plan.goal_id,
        knowledge_node_id=knowledge_node_id,
        knowledge_node_code=knowledge_node_code,
        title=title,
        task_type=task_type,
        objective=objective,
        scheduled_date=date.today() + timedelta(days=max(0, scheduled_day - 1)),
        scheduled_day=scheduled_day,
        estimated_minutes=estimated_minutes,
        priority=priority,
        status="pending",
        payload=payload,
        origin="planner",
    )
    session.add(task)
    return task


def _clone_plan_task(
    session: Session,
    *,
    source: PlanTask,
    plan: LearningPlan,
    scheduled_day: int,
    estimated_minutes: int,
    adjustment_id: str,
) -> PlanTask:
    payload = dict(source.payload or {})
    payload.update({"source": "plan_adjustment", "adjustment_id": adjustment_id, "previous_task_id": source.id})
    return _add_plan_task(
        session,
        plan=plan,
        knowledge_node_id=source.knowledge_node_id,
        knowledge_node_code=source.knowledge_node_code,
        title=source.title,
        task_type=source.task_type,
        objective=source.objective,
        scheduled_day=scheduled_day,
        estimated_minutes=estimated_minutes,
        priority=source.priority,
        payload=payload,
    )


def _review_nodes_for_adjustment(
    session: Session,
    *,
    adjustment: PlanAdjustmentRecord,
    snapshot: LearningStateSnapshot | None,
    fallback_tasks: list[PlanTask],
) -> list[dict]:
    evidence = _json_dict(adjustment.evidence_json)
    signals = _json_dict(evidence.get("observer_signals", {}))
    candidates = list(signals.get("low_mastery_nodes") or [])
    if snapshot is not None:
        candidates.extend((snapshot.current_state or {}).get("review_queue", []))
    seen: set[str] = set()
    nodes: list[dict] = []
    for item in candidates:
        node_id = item.get("knowledge_node_id")
        if not node_id or node_id in seen:
            continue
        node = session.get(KnowledgeNode, node_id)
        if node is None:
            continue
        seen.add(node_id)
        nodes.append({"id": node.id, "code": node.code})
    for task in fallback_tasks:
        if task.knowledge_node_id not in seen:
            seen.add(task.knowledge_node_id)
            nodes.append({"id": task.knowledge_node_id, "code": task.knowledge_node_code})
    return nodes


def _next_uncovered_node(session: Session, *, previous_plan: LearningPlan, tasks: list[PlanTask]) -> KnowledgeNode | None:
    covered = {task.knowledge_node_id for task in tasks}
    nodes = list(
        session.scalars(
            select(KnowledgeNode)
            .where(KnowledgeNode.curriculum_id == previous_plan.curriculum_id)
            .order_by(KnowledgeNode.sequence)
        )
    )
    for node in nodes:
        if node.id not in covered:
            return node
    return nodes[-1] if nodes else None


def _next_plan_version(session: Session, user_id: str, goal_id: str) -> int:
    versions = session.scalars(
        select(LearningPlan.version).where(LearningPlan.user_id == user_id, LearningPlan.goal_id == goal_id)
    ).all()
    return max(versions, default=0) + 1


def _json_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _to_iso(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _run_engine(session: Session, request: TutorRunRequest) -> TutorRunResult:
    embedding = DeterministicEmbeddingClient()
    dependencies = Phase2Dependencies(
        state_repository=SQLAlchemyStateRepository(session),
        rag_repository=SQLAlchemyRagRepository(session, embedding),
        assessment_repository=SQLAlchemyAssessmentRepository(session, request.user_id, request.goal_id),
        plan_repository=SQLAlchemyPlanRepository(session),
        audit_sink=SQLAlchemyAuditSink(session),
        llm_client=LLMGatewayClient(),
        embedding_client=embedding,
        ocr_client=NoopOCRClient(),
        assessment_factory=build_assessment_draft,
    )
    return Phase2TutorEngine(dependencies).run(request)


def _load_snapshot(session: Session, *, user_id: str, goal_id: str) -> LearningStateSnapshot | None:
    return session.scalar(
        select(LearningStateSnapshot).where(
            LearningStateSnapshot.user_id == user_id,
            LearningStateSnapshot.goal_id == goal_id,
        )
    )


def _default_citations(query: str) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id="chunk-stage3-rag",
            document_id="doc-curated-stage3",
            content=f"Curated AI app development note related to: {query}",
            citation_label="AI App Dev V1 - RAG Foundations",
            source_title="LangChain RAG Concepts",
            source_url="https://docs.langchain.com/oss/python/langchain/rag",
            trusted_level=4,
            metadata={"source_type": "curated", "untrusted_input": False},
        )
    ]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(a * a for a in left)) or 1.0
    right_norm = sqrt(sum(b * b for b in right)) or 1.0
    return dot / (left_norm * right_norm)


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


def _upsert_phase_state(
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


def _refresh_phase_state_after_submit(
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


def _run_result_to_dict(result: TutorRunResult) -> dict:
    return {
        "route": result.route,
        "final_answer": result.final_answer,
        "citations": [item.model_dump() for item in result.citations],
        "assessment_draft": result.assessment_draft.model_dump() if result.assessment_draft else None,
        "assessment_result": result.assessment_result.model_dump() if result.assessment_result else None,
        "mastery_updates": [item.model_dump() for item in result.mastery_updates],
        "observer_decision": result.observer_decision.model_dump() if result.observer_decision else None,
        "plan_adjustment": result.plan_adjustment.model_dump() if result.plan_adjustment else None,
        "audit_log": result.audit_log,
    }


def _draft_to_dict(draft: AssessmentDraft) -> dict:
    return {
        "assessment_id": draft.assessment_id,
        "assessment_type": draft.assessment_type,
        "status": "active",
        "scope": draft.scope,
        "items": [item.model_dump() for item in draft.items],
    }


def _plan_adjustment_model_to_dict(adjustment: PlanAdjustment) -> dict:
    return {
        "adjustment_id": adjustment.adjustment_id,
        "user_id": adjustment.user_id,
        "goal_id": adjustment.goal_id,
        "previous_plan_id": adjustment.previous_plan_id,
        "new_plan_id": adjustment.new_plan_id,
        "trigger_type": adjustment.trigger_type,
        "decision": adjustment.decision,
        "status": adjustment.status,
        "evidence_json": adjustment.evidence_json,
        "before_snapshot": adjustment.before_snapshot,
        "after_snapshot": adjustment.after_snapshot,
        "plan_patch": adjustment.plan_patch,
        "change_summary": adjustment.change_summary,
        "rationale_json": adjustment.rationale_json,
    }


def _plan_adjustment_record_to_dict(record: PlanAdjustmentRecord) -> dict:
    return {
        "adjustment_id": record.id,
        "user_id": record.user_id,
        "goal_id": record.goal_id,
        "previous_plan_id": record.previous_plan_id,
        "new_plan_id": record.new_plan_id,
        "trigger_type": record.trigger_type,
        "decision": record.decision,
        "status": record.status,
        "evidence_json": _json_dict(record.evidence_json),
        "before_snapshot": _json_dict(record.before_snapshot),
        "after_snapshot": _json_dict(record.after_snapshot),
        "plan_patch": _json_dict(record.plan_patch),
        "change_summary": _json_dict(record.change_summary),
        "rationale_json": _json_dict(record.rationale_json),
        "created_at": _to_iso(record.created_at),
    }


def _task_to_dict(task: PlanTask) -> dict:
    return {
        "id": task.id,
        "knowledge_node_code": task.knowledge_node_code,
        "knowledge_node_id": task.knowledge_node_id,
        "knowledge_node_title": task.knowledge_node_code.replace("_", " ").title(),
        "title": task.title,
        "objective": task.objective,
        "task_type": task.task_type,
        "scheduled_date": _to_iso(task.scheduled_date),
        "estimated_minutes": task.estimated_minutes,
        "status": task.status,
    }


def _learning_session_to_dict(record: LearningSession) -> dict:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "goal_id": record.goal_id,
        "plan_id": record.plan_id,
        "task_id": record.task_id,
        "started_at": _to_iso(record.started_at),
        "ended_at": _to_iso(record.ended_at),
        "duration_minutes": record.duration_minutes,
        "status": record.status,
        "evidence_json": record.evidence_json,
    }


def _learning_event_to_dict(record: LearningEvent) -> dict:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "goal_id": record.goal_id,
        "session_id": record.session_id,
        "task_id": record.task_id,
        "event_type": record.event_type,
        "source": record.source,
        "event_payload": record.event_payload,
        "occurred_at": _to_iso(record.occurred_at),
    }


def _document_to_dict(document: Document) -> dict:
    return {
        "id": document.id,
        "owner_user_id": document.owner_user_id,
        "corpus_type": document.corpus_type,
        "filename": document.filename,
        "object_key": document.object_key,
        "mime_type": document.mime_type,
        "parse_status": document.parse_status,
        "sha256": document.sha256,
        "source_url": document.source_url,
        "trusted_level": document.trusted_level,
        "created_at": document.created_at,
    }
