from __future__ import annotations

import pytest

from adaptive_tutor.phase2.assessment_intelligence import (
    AssessmentBlueprint,
    AssessmentItemProposal,
    AssessmentItemValidator,
    GraderRouter,
    MasteryPolicyResult,
    apply_mastery_policy,
    build_intelligent_assessment_draft,
)
from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.schemas import TutorRunRequest


def test_low_confidence_mastery_is_pending_review_and_unchanged() -> None:
    result = apply_mastery_policy(
        historical_mastery=0.7,
        validated_score=0.9,
        confidence=0.54,
        evidence_count=3,
        elapsed_days=0,
    )
    assert isinstance(result, MasteryPolicyResult)
    assert result.status == "pending_review"
    assert result.new_mastery == 0.7


def test_mastery_policy_applies_decay_and_bounded_delta() -> None:
    result = apply_mastery_policy(
        historical_mastery=0.5,
        validated_score=1.0,
        confidence=1.0,
        evidence_count=3,
        elapsed_days=30,
    )
    assert result.status == "updated"
    assert result.new_mastery == pytest.approx(0.65)
    assert result.delta <= 0.15


def test_item_validator_rejects_source_outside_allowed_snapshot() -> None:
    blueprint = AssessmentBlueprint(
        item_count=1,
        knowledge_node_ids=("rag",),
        allowed_source_chunk_ids=("chunk-1",),
        question_type_distribution={"explain": 1},
        difficulty_distribution={2: 1},
    )
    proposal = AssessmentItemProposal(
        question="Explain RAG.",
        question_type="explain",
        knowledge_node_id="rag",
        difficulty=2,
        reference_answer="retrieval",
        rubric={"retrieval": 1},
        source_chunk_ids=("chunk-external",),
    )
    with pytest.raises(ValueError, match="source chunk"):
        AssessmentItemValidator().validate(proposal, blueprint, prior_questions=())


def test_grader_router_scores_objective_answer_deterministically() -> None:
    grade = GraderRouter().grade_objective(expected=["b", "c"], actual=["c", "b"])
    assert grade.score == 1.0
    assert grade.confidence == 1.0
    assert grade.grader_type == "objective_rule"


def test_intelligent_builder_records_blueprint_and_validated_items() -> None:
    draft = build_intelligent_assessment_draft("daily", ["rag"])
    assert draft.scope["blueprint"]["item_count"] == 3
    assert len(draft.items) == 3
    assert len({item.prompt for item in draft.items}) == 3


def test_assessment_flag_routes_engine_through_blueprint_builder(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_ASSESSMENT_INTELLIGENCE_V2", "true")
    dependencies = build_mock_phase2_dependencies()
    result = Phase2TutorEngine(dependencies).run(
        TutorRunRequest(
            trigger_type="assessment_due",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="t3-assessment-thread",
            assessment_type="daily",
            knowledge_node_ids=["rag"],
        )
    )
    assert result.assessment_draft is not None
    assert result.assessment_draft.scope["blueprint"]["item_count"] == 3
