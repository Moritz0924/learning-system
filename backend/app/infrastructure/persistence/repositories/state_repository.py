from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from adaptive_tutor.phase2.replanning import build_observer_signals
from backend.app.application.learning_activity_service import _recent_learning_events
from backend.app.models import (
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    LearningEvent,
    LearningStateSnapshot,
    MasteryRecord,
    PhaseAssessmentState,
    PlanTask,
)


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
