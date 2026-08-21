from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.app.domain.assessment.contracts import (
    AssessmentGenerationBundleV2,
    AssessmentGenerationContextV2,
    AssessmentGenerationPolicy,
    AssessmentGoalContext,
    AssessmentKnowledgeNodeContext,
    AssessmentMasteryContext,
    GeneratedAssessmentItemV2,
    GeneratedOptionV2,
    RubricCriterionV2,
)
from backend.app.models import Assessment, AssessmentAnswer, AssessmentAttempt, MasteryRecord, PlanAdjustmentRecord


def _context() -> AssessmentGenerationContextV2:
    return AssessmentGenerationContextV2(
        schema_version="assessment-generation-context-v2",
        user_id="user-1",
        goal_id="goal-1",
        assessment_type="daily",
        requested_item_count=1,
        requested_knowledge_node_ids=["node-python"],
        goal=AssessmentGoalContext(title="Learn Python", target_outcome="Build an API"),
        current_task=None,
        knowledge_nodes=[
            AssessmentKnowledgeNodeContext(
                knowledge_node_id="node-python",
                code="python_foundations",
                title="Python Foundations",
                learning_objectives=["Explain functions"],
                prerequisites=[],
                difficulty=1,
                mastery_threshold=70,
                common_misconceptions=[],
            )
        ],
        mastery=[
            AssessmentMasteryContext(
                knowledge_node_id="node-python",
                score=60,
                confidence=0.5,
                last_evidence_at=datetime.now(timezone.utc),
            )
        ],
        recent_misconceptions=[],
        recent_attempt_summaries=[],
        source_excerpts=[],
        generation_policy=AssessmentGenerationPolicy(),
        context_hash="a" * 64,
    )


def test_generation_contract_forbids_extra_fields_and_non_finite_scores() -> None:
    context = _context()
    assert context.schema_version == "assessment-generation-context-v2"

    with pytest.raises(ValidationError):
        AssessmentGenerationContextV2(**{**context.model_dump(), "unexpected": "value"})

    with pytest.raises(ValidationError):
        AssessmentMasteryContext(
            knowledge_node_id="node-python",
            score=float("nan"),
            confidence=0.5,
            last_evidence_at=None,
        )


def test_generation_bundle_has_no_database_identifiers() -> None:
    bundle = AssessmentGenerationBundleV2(
        schema_version="assessment-generation-v2",
        generator_version="assessment-generator-v2",
        items=[
            GeneratedAssessmentItemV2(
                item_key="python-choice-1",
                knowledge_node_id="node-python",
                question_type="choice",
                target_skill="recall",
                prompt="Which statement creates a Python function?",
                options=[
                    GeneratedOptionV2(option_key="a", label="def greet():"),
                    GeneratedOptionV2(option_key="b", label="function greet()"),
                ],
                reference_answer="a",
                rubric=[
                    RubricCriterionV2(
                        criterion_id="correct-choice",
                        description="Select the valid Python declaration.",
                        max_points=100,
                        required_evidence=["a"],
                        accepted_concepts=["def"],
                        common_error_tags=["invalid_function_syntax"],
                    )
                ],
                difficulty=1,
                source_chunk_ids=[],
            )
        ],
    )

    assert bundle.items[0].reference_answer == "a"
    with pytest.raises(ValidationError):
        GeneratedAssessmentItemV2(**{**bundle.items[0].model_dump(), "assessment_id": "bad"})


def test_v2_persistence_metadata_is_exposed_by_the_orm() -> None:
    assert Assessment.generation_request_id
    assert Assessment.generation_input_hash
    assert Assessment.schema_version
    assert AssessmentAttempt.request_id
    assert AssessmentAttempt.answer_payload_hash
    assert AssessmentAttempt.lease_expires_at
    assert AssessmentAnswer.needs_review
    assert MasteryRecord.calculation_version
    assert MasteryRecord.last_evidence_at
    assert PlanAdjustmentRecord.policy_version
    assert PlanAdjustmentRecord.automation_allowed
