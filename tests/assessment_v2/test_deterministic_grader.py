from __future__ import annotations

import pytest

from backend.app.domain.assessment.contracts import (
    AssessmentGradingContextV2,
    AssessmentItemForGrading,
    GeneratedOptionV2,
    RubricCriterionV2,
)
from backend.app.domain.assessment.errors import AssessmentDomainError
from backend.app.domain.assessment.grading_policy import deterministic_grade, validate_grade_bundle


def _context(answers: dict[str, str]) -> AssessmentGradingContextV2:
    criterion = RubricCriterionV2(
        criterion_id="correct",
        description="Correct response",
        max_points=100,
        required_evidence=[],
        accepted_concepts=["correct"],
        common_error_tags=["incorrect"],
    )
    return AssessmentGradingContextV2(
        schema_version="assessment-grading-context-v2",
        assessment_id="assessment-1",
        attempt_id="attempt-1",
        assessment_type="daily",
        items=[
            AssessmentItemForGrading(
                item_id="choice-1",
                knowledge_node_id="node-1",
                question_type="choice",
                prompt="Choose the correct option.",
                options=[GeneratedOptionV2(option_key="a", label="Correct"), GeneratedOptionV2(option_key="b", label="Wrong")],
                reference_answer="a",
                rubric=[criterion],
                difficulty=1,
            ),
            AssessmentItemForGrading(
                item_id="open-1",
                knowledge_node_id="node-1",
                question_type="explain",
                prompt="Explain the answer.",
                reference_answer="A reference answer",
                rubric=[criterion],
                difficulty=2,
            ),
        ],
        submitted_answers=answers,
        grading_policy_version="assessment-grading-policy-v2",
        context_hash="b" * 64,
    )


def test_blank_and_choice_answers_are_graded_deterministically() -> None:
    result = deterministic_grade(_context({"choice-1": "a", "open-1": ""}))
    grades = {item.item_id: item for item in result.item_grades}

    assert grades["choice-1"].criterion_grades[0].points_awarded == 100
    assert grades["choice-1"].confidence == 1
    assert grades["open-1"].criterion_grades[0].points_awarded == 0
    assert grades["open-1"].wrong_reason_tags == ["unanswered"]


def test_grade_validation_rejects_evidence_not_present_in_submitted_answer() -> None:
    context = _context({"choice-1": "a", "open-1": "answer"})
    bundle = deterministic_grade(context)
    forged = bundle.model_copy(
        update={
            "item_grades": [
                *bundle.item_grades[:1],
                bundle.item_grades[1].model_copy(
                    update={
                        "criterion_grades": [
                            bundle.item_grades[1].criterion_grades[0].model_copy(update={"evidence_quote": "not in answer"})
                        ]
                    }
                ),
            ]
        }
    )

    with pytest.raises(AssessmentDomainError, match="evidence"):
        validate_grade_bundle(context, forged)
