"""Semantic grounding grade; never invents quality metrics without a judge or human."""
from __future__ import annotations

from evals.models import GroundingGrade, JudgeVerdict, LearningQaEvaluationCase


def grade_grounding(
    case: LearningQaEvaluationCase,
    *,
    judge_verdict: JudgeVerdict | None,
    judge_error: bool = False,
) -> GroundingGrade:
    if judge_error:
        return GroundingGrade(
            contains_unsupported_claim=None,
            correctly_abstained=None,
            unsupported_claim_reason=None,
            semantic_grade_status="judge_error",
        )
    if judge_verdict is None:
        return GroundingGrade(
            contains_unsupported_claim=None,
            correctly_abstained=None,
            unsupported_claim_reason=None,
            semantic_grade_status="not_graded",
        )
    return GroundingGrade(
        contains_unsupported_claim=judge_verdict.contains_unsupported_claim,
        correctly_abstained=(judge_verdict.correctly_abstained if not case.is_answerable else None),
        unsupported_claim_reason=judge_verdict.reason,
        semantic_grade_status="judge_graded",
    )
