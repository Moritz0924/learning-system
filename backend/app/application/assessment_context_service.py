from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domain.assessment.contracts import (
    AssessmentGenerationContextV2,
    AssessmentGenerationPolicy,
    AssessmentGoalContext,
    AssessmentGradingContextV2,
    AssessmentItemForGrading,
    AssessmentKnowledgeNodeContext,
    AssessmentMasteryContext,
    AssessmentSourceExcerpt,
    AssessmentTaskContext,
    GeneratedOptionV2,
    RecentAttemptSummary,
    RubricCriterionV2,
)
from backend.app.domain.assessment.errors import AssessmentDomainError
from backend.app.models import (
    Assessment,
    AssessmentAttempt,
    AssessmentItem,
    KnowledgeEdge,
    KnowledgeNode,
    LearningGoal,
    MasteryRecord,
    PlanTask,
)
from backend.app.infrastructure.persistence.repositories.rag_repository import SQLAlchemyRagRepository
from backend.app.services.embeddings import build_embedding_client


def canonical_json_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_answers(answers: dict[str, str]) -> dict[str, str]:
    return {
        key: unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
        for key, value in sorted(answers.items())
    }


class AssessmentContextService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def build_generation(
        self,
        *,
        user_id: str,
        goal_id: str,
        assessment_type: str,
        knowledge_node_ids: list[str],
    ) -> AssessmentGenerationContextV2:
        goal = self.session.scalar(select(LearningGoal).where(LearningGoal.id == goal_id, LearningGoal.user_id == user_id))
        if goal is None:
            raise LookupError(f"learning goal {goal_id} not found")
        nodes = list(self.session.scalars(select(KnowledgeNode).where(KnowledgeNode.id.in_(knowledge_node_ids))))
        by_id = {node.id: node for node in nodes}
        missing = [node_id for node_id in knowledge_node_ids if node_id not in by_id]
        if missing:
            raise LookupError(f"knowledge node {missing[0]} not found")
        edges = list(
            self.session.scalars(
                select(KnowledgeEdge).where(KnowledgeEdge.to_node_id.in_(knowledge_node_ids), KnowledgeEdge.relation_type == "prerequisite")
            )
        )
        source_nodes = {edge.from_node_id for edge in edges}
        prerequisite_codes = {
            node.id: node.code
            for node in self.session.scalars(select(KnowledgeNode).where(KnowledgeNode.id.in_(source_nodes)))
        }
        mastery_records = {
            record.knowledge_node_id: record
            for record in self.session.scalars(
                select(MasteryRecord).where(MasteryRecord.user_id == user_id, MasteryRecord.goal_id == goal_id)
            )
        }
        task = self.session.scalar(
            select(PlanTask)
            .where(PlanTask.user_id == user_id, PlanTask.goal_id == goal_id, PlanTask.status.in_(["active", "pending"]))
            .order_by(PlanTask.scheduled_day, PlanTask.priority, PlanTask.id)
        )
        recent = list(
            self.session.scalars(
                select(AssessmentAttempt)
                .where(AssessmentAttempt.user_id == user_id)
                .order_by(AssessmentAttempt.completed_at.desc(), AssessmentAttempt.submitted_at.desc())
                .limit(10)
            )
        )
        policy = AssessmentGenerationPolicy()
        excerpts = self._retrieve_excerpts(user_id=user_id, query=" ".join(node.title for node in nodes), policy=policy)
        payload = {
            "schema_version": "assessment-generation-context-v2",
            "user_id": user_id,
            "goal_id": goal_id,
            "assessment_type": assessment_type,
            "requested_item_count": {"daily": 3, "weekly": 10, "phase": 4}[assessment_type],
            "requested_knowledge_node_ids": knowledge_node_ids,
            "goal": {"title": goal.title, "target_outcome": goal.target_outcome},
            "current_task": (
                {
                    "task_id": task.id,
                    "title": task.title,
                    "objective": task.objective,
                    "knowledge_node_ids": [task.knowledge_node_id],
                }
                if task is not None
                else None
            ),
            "knowledge_nodes": [
                {
                    "knowledge_node_id": node.id,
                    "code": node.code,
                    "title": node.title,
                    "learning_objectives": list((node.metadata_json or {}).get("learning_objectives", [node.title])),
                    "prerequisites": [prerequisite_codes[edge.from_node_id] for edge in edges if edge.to_node_id == node.id and edge.from_node_id in prerequisite_codes],
                    "difficulty": node.difficulty,
                    "mastery_threshold": node.mastery_threshold,
                    "common_misconceptions": list((node.metadata_json or {}).get("common_misconceptions", [])),
                }
                for node in nodes
            ],
            "mastery": [
                {
                    "knowledge_node_id": node.id,
                    "score": mastery_records.get(node.id).mastery_score if node.id in mastery_records else 60,
                    "confidence": mastery_records.get(node.id).confidence if node.id in mastery_records else 0.1,
                    "last_evidence_at": mastery_records.get(node.id).last_evidence_at if node.id in mastery_records else None,
                }
                for node in nodes
            ],
            "recent_misconceptions": [],
            "recent_attempt_summaries": [
                {
                    "assessment_id": attempt.assessment_id,
                    "score": attempt.score,
                    "status": attempt.status,
                    "completed_at": attempt.completed_at,
                }
                for attempt in recent
            ],
            "source_excerpts": [excerpt.model_dump(mode="json") for excerpt in excerpts],
            "generation_policy": policy.model_dump(),
        }
        return AssessmentGenerationContextV2(**payload, context_hash=canonical_json_hash(payload))

    def build_grading(self, *, assessment: Assessment, attempt: AssessmentAttempt) -> AssessmentGradingContextV2:
        items = list(self.session.scalars(select(AssessmentItem).where(AssessmentItem.assessment_id == assessment.id).order_by(AssessmentItem.id)))
        grading_items = [self._grading_item(item) for item in items]
        answers = normalize_answers(dict(attempt.submitted_answers_json or {}))
        payload = {
            "schema_version": "assessment-grading-context-v2",
            "assessment_id": assessment.id,
            "attempt_id": attempt.id,
            "assessment_type": assessment.assessment_type,
            "items": [item.model_dump(mode="json") for item in grading_items],
            "submitted_answers": answers,
            "grading_policy_version": "assessment-grading-policy-v2",
        }
        return AssessmentGradingContextV2(**payload, context_hash=canonical_json_hash(payload))

    def validate_answer_item_ids(self, assessment_id: str, answers: dict[str, str]) -> None:
        known = set(self.session.scalars(select(AssessmentItem.id).where(AssessmentItem.assessment_id == assessment_id)))
        unknown = set(answers) - known
        if unknown:
            raise AssessmentDomainError("Submitted answers contain unknown assessment item IDs.", code="assessment.unknown_item_id")

    def _retrieve_excerpts(self, *, user_id: str, query: str, policy: AssessmentGenerationPolicy) -> list[AssessmentSourceExcerpt]:
        try:
            chunks = SQLAlchemyRagRepository(self.session, build_embedding_client()).retrieve(query, top_k=policy.max_source_excerpts, user_id=user_id)
        except Exception:
            return []
        remaining = policy.max_context_chars
        excerpts: list[AssessmentSourceExcerpt] = []
        for chunk in chunks:
            content = chunk.content[: min(1500, remaining)]
            if not content:
                break
            excerpts.append(
                AssessmentSourceExcerpt(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    citation_label=chunk.citation_label,
                    content=content,
                    trusted_level=chunk.trusted_level,
                    untrusted_input=bool(chunk.metadata.get("untrusted_input", True)),
                )
            )
            remaining -= len(content)
        return excerpts

    @staticmethod
    def _grading_item(item: AssessmentItem) -> AssessmentItemForGrading:
        options_data = (item.options_json or {}).get("options", [])
        rubric_data = item.rubric_json or {}
        criteria_data = rubric_data.get("criteria")
        if not criteria_data:
            criteria_data = [
                {
                    "criterion_id": "legacy-score",
                    "description": "Legacy assessment criterion.",
                    "max_points": 100,
                    "required_evidence": [],
                    "accepted_concepts": [],
                    "common_error_tags": [],
                    "deterministic_signals": [],
                }
            ]
        return AssessmentItemForGrading(
            item_id=item.id,
            knowledge_node_id=item.knowledge_node_id,
            question_type=item.question_type,
            prompt=item.prompt,
            options=[GeneratedOptionV2(option_key=option["option_id"], label=option["label"]) for option in options_data],
            reference_answer=item.reference_answer,
            rubric=[RubricCriterionV2.model_validate(criterion) for criterion in criteria_data],
            difficulty=item.difficulty,
        )


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported canonical JSON value: {type(value)!r}")
