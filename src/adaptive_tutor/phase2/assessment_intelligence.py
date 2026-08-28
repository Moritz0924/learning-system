"""Deterministic assessment contracts used by the optional T3 path."""

from __future__ import annotations

from uuid import uuid4
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from adaptive_tutor.tutor.t3_contracts import MasteryPolicy
from .schemas import AssessmentDraft, AssessmentItem, AssessmentAttemptResult, MasteryUpdate, AssessmentType


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AssessmentBlueprint(_FrozenModel):
    item_count: int = Field(ge=1)
    knowledge_node_ids: tuple[str, ...]
    allowed_source_chunk_ids: tuple[str, ...] = ()
    question_type_distribution: dict[str, int]
    difficulty_distribution: dict[int, int]


class AssessmentItemProposal(_FrozenModel):
    question: str = Field(min_length=1)
    question_type: str = Field(min_length=1)
    knowledge_node_id: str = Field(min_length=1)
    difficulty: int = Field(ge=1, le=5)
    reference_answer: str = Field(min_length=1)
    rubric: dict
    source_chunk_ids: tuple[str, ...] = ()


class ValidatedGrade(_FrozenModel):
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    grader_type: str
    evidence: tuple[str, ...] = ()
    status: str = "validated"


class MasteryPolicyResult(_FrozenModel):
    status: str
    new_mastery: float = Field(ge=0, le=1)
    delta: float
    prior: float = Field(ge=0, le=1)


class AssessmentItemValidator:
    def validate(
        self,
        proposal: AssessmentItemProposal,
        blueprint: AssessmentBlueprint,
        *,
        prior_questions: Sequence[str],
    ) -> AssessmentItemProposal:
        if proposal.knowledge_node_id not in blueprint.knowledge_node_ids:
            raise ValueError("knowledge node is outside blueprint")
        if any(source not in blueprint.allowed_source_chunk_ids for source in proposal.source_chunk_ids):
            raise ValueError("source chunk is outside blueprint")
        if proposal.question.strip().casefold() in {item.strip().casefold() for item in prior_questions}:
            raise ValueError("duplicate assessment question")
        if proposal.reference_answer.casefold() in proposal.question.casefold():
            raise ValueError("reference answer leaked into question")
        return proposal


class GraderRouter:
    @staticmethod
    def grade_objective(*, expected: Sequence[str], actual: Sequence[str]) -> ValidatedGrade:
        expected_set = {item.strip().casefold() for item in expected}
        actual_set = {item.strip().casefold() for item in actual}
        score = 1.0 if expected_set == actual_set else 0.0
        return ValidatedGrade(
            score=score,
            confidence=1.0,
            grader_type="objective_rule",
            evidence=("exact_set_match",) if score else ("objective_mismatch",),
        )


def build_assessment_blueprint(
    assessment_type: AssessmentType,
    knowledge_node_ids: Sequence[str],
) -> AssessmentBlueprint:
    counts = {"daily": 3, "weekly": 10, "phase": 4}
    item_count = counts[assessment_type]
    nodes = tuple(knowledge_node_ids or ("general_foundations",))
    question_types = {"choice": 0, "explain": 0, "code_reading": 0}
    difficulties: dict[int, int] = {}
    for index in range(item_count):
        question_type = ("choice", "explain", "code_reading")[index % 3]
        difficulty = 2 + index % 3
        question_types[question_type] += 1
        difficulties[difficulty] = difficulties.get(difficulty, 0) + 1
    return AssessmentBlueprint(
        item_count=item_count,
        knowledge_node_ids=nodes,
        question_type_distribution={key: value for key, value in question_types.items() if value},
        difficulty_distribution=difficulties,
    )


def build_intelligent_assessment_draft(
    assessment_type: AssessmentType,
    knowledge_node_ids: list[str],
    *,
    source_chunk_ids: list[str] | None = None,
    locale: str = "en-US",
    node_labels: dict[str, str] | None = None,
) -> AssessmentDraft:
    blueprint = build_assessment_blueprint(assessment_type, knowledge_node_ids)
    validator = AssessmentItemValidator()
    proposals: list[AssessmentItemProposal] = []
    labels = node_labels or {}
    for index in range(blueprint.item_count):
        node_id = blueprint.knowledge_node_ids[index % len(blueprint.knowledge_node_ids)]
        question_type = ("choice", "explain", "code_reading")[index % 3]
        difficulty = 2 + index % 3
        label = labels.get(node_id) or ("该知识点" if locale == "zh-CN" else "this topic")
        proposal = AssessmentItemProposal(
            question=(
                f"请把“{label}”应用到学习场景 {index + 1}。"
                if locale == "zh-CN"
                else f"Apply {label} to learning case {index + 1}."
            ),
            question_type=question_type,
            knowledge_node_id=node_id,
            difficulty=difficulty,
            reference_answer=(
                f"正确答案应结合具体推理解释“{label}”。"
                if locale == "zh-CN"
                else f"A correct answer explains {label} with concrete reasoning."
            ),
            rubric={"knowledge_node": node_id, "max_score": 100},
            source_chunk_ids=tuple(source_chunk_ids or ()),
        )
        proposals.append(validator.validate(proposal, blueprint, prior_questions=[item.question for item in proposals]))
    return AssessmentDraft(
        assessment_id=f"assessment-{uuid4()}",
        assessment_type=assessment_type,
        status="draft",
        scope={"knowledge_node_ids": list(blueprint.knowledge_node_ids), "blueprint": blueprint.model_dump(), "locale": locale},
        items=[
            AssessmentItem(
                item_id=f"item-{uuid4()}",
                knowledge_node_id=proposal.knowledge_node_id,
                question_type=proposal.question_type,
                prompt=proposal.question,
                options_json=(
                    {"options": [
                        {"option_id": "a", "label": "采用文档化的安全做法。"},
                        {"option_id": "b", "label": "跳过验证。"},
                    ]}
                    if locale == "zh-CN"
                    else {"options": [
                        {"option_id": "a", "label": "Use the documented safe approach."},
                        {"option_id": "b", "label": "Skip validation."},
                    ]}
                ) if proposal.question_type == "choice" else {},
                reference_answer=proposal.reference_answer,
                rubric_json=proposal.rubric,
                difficulty=proposal.difficulty,
                source_chunk_ids=list(proposal.source_chunk_ids),
            )
            for proposal in proposals
        ],
    )


def mastery_updates_from_attempt_v2(
    draft: AssessmentDraft,
    result: AssessmentAttemptResult,
    current_mastery: dict,
    *,
    policy: MasteryPolicy | None = None,
) -> list[MasteryUpdate]:
    updates: list[MasteryUpdate] = []
    policy = policy or MasteryPolicy()
    for node_id in sorted({item.knowledge_node_id for item in draft.items}):
        scores = [
            answer.score / 100
            for answer in result.answers
            for item in draft.items
            if item.item_id == answer.item_id and item.knowledge_node_id == node_id
        ]
        if not scores:
            continue
        confidence = min(
            answer.confidence
            for answer in result.answers
            for item in draft.items
            if item.item_id == answer.item_id and item.knowledge_node_id == node_id
        )
        previous = float(current_mastery.get(node_id, {}).get("score", 50)) / 100
        calculated = apply_mastery_policy(
            historical_mastery=previous,
            validated_score=sum(scores) / len(scores),
            confidence=confidence,
            evidence_count=len(scores),
            elapsed_days=0,
            policy=policy,
        )
        if calculated.status != "updated":
            continue
        updates.append(
            MasteryUpdate(
                knowledge_node_id=node_id,
                previous_score=round(previous * 100, 2),
                new_score=round(calculated.new_mastery * 100, 2),
                confidence=confidence,
                evidence_count=len(scores),
                calculation_version="t3-mastery-policy-v1",
                source_breakdown={"validated_score": sum(scores) / len(scores), "prior": calculated.prior},
                missing_data_strategy={},
            )
        )
    return updates


def apply_mastery_policy(
    *,
    historical_mastery: float,
    validated_score: float,
    confidence: float,
    evidence_count: int,
    elapsed_days: float,
    policy: MasteryPolicy | None = None,
) -> MasteryPolicyResult:
    policy = policy or MasteryPolicy()
    _validate_unit_interval(historical_mastery, "historical_mastery")
    _validate_unit_interval(validated_score, "validated_score")
    _validate_unit_interval(confidence, "confidence")
    if evidence_count < 1 or elapsed_days < 0:
        raise ValueError("evidence_count must be >= 1 and elapsed_days must be >= 0")
    if confidence < policy.minimum_confidence_for_update:
        return MasteryPolicyResult(
            status="pending_review",
            new_mastery=historical_mastery,
            delta=0.0,
            prior=historical_mastery,
        )
    decay_factor = 0.5 ** (elapsed_days / policy.decay_half_life_days)
    prior = policy.neutral_mastery + (historical_mastery - policy.neutral_mastery) * decay_factor
    evidence_weight = min(1.0, evidence_count / policy.evidence_count_for_full_weight)
    alpha = policy.base_learning_rate * confidence * evidence_weight
    raw_mastery = prior + alpha * (validated_score - prior)
    raw_delta = raw_mastery - historical_mastery
    bounded_delta = min(policy.max_positive_delta, max(-policy.max_negative_delta, raw_delta))
    new_mastery = min(1.0, max(0.0, historical_mastery + bounded_delta))
    return MasteryPolicyResult(
        status="updated",
        new_mastery=round(new_mastery, 6),
        delta=round(new_mastery - historical_mastery, 6),
        prior=round(prior, 6),
    )


def _validate_unit_interval(value: float, name: str) -> None:
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
