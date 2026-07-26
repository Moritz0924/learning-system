from __future__ import annotations

from evals.models import JudgeVerdict, LearningQaEvaluationCase


def _case(answerable: bool) -> LearningQaEvaluationCase:
    return LearningQaEvaluationCase.model_validate({
        "case_id": "case",
        "dataset_version": "v1",
        "split": "development",
        "category": "single_source" if answerable else "unanswerable",
        "difficulty": "easy",
        "question": "q",
        "conversation_history": [],
        "gold_answer_points": ["a"] if answerable else [],
        "gold_document_ids": ["d"] if answerable else [],
        "gold_evidence_spans": ([{"evidence_id": "e", "document_id": "d", "text": "a"}] if answerable else []),
        "is_answerable": answerable,
        "expected_behavior": "answer_with_citation" if answerable else "abstain",
        "format_contract": {"type": "strict_json", "require_citations": answerable},
    })


def test_grounding_semantic_metrics_are_null_without_judge_or_human() -> None:
    from evals.graders.grounding_grader import grade_grounding

    grade = grade_grounding(_case(True), judge_verdict=None)
    assert grade.contains_unsupported_claim is None
    assert grade.correctly_abstained is None
    assert grade.semantic_grade_status == "not_graded"


def test_judge_verdict_populates_semantic_grounding_fields() -> None:
    from evals.graders.grounding_grader import grade_grounding

    verdict = JudgeVerdict(
        citation_supported=True,
        contains_unsupported_claim=False,
        correctly_abstained=True,
        reason="supported refusal",
    )
    grade = grade_grounding(_case(False), judge_verdict=verdict)
    assert grade.contains_unsupported_claim is False
    assert grade.correctly_abstained is True
    assert grade.semantic_grade_status == "judge_graded"
