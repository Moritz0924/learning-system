from __future__ import annotations

from statistics import mean

from .contracts import (
    AssessmentGradeBundleV2,
    AssessmentGradingContextV2,
    CriterionGradeV2,
    ItemGradeV2,
)
from .errors import AssessmentDomainError


def deterministic_grade(context: AssessmentGradingContextV2) -> AssessmentGradeBundleV2:
    grades: list[ItemGradeV2] = []
    for item in context.items:
        answer = context.submitted_answers.get(item.item_id, "")
        if not answer.strip():
            grades.append(
                ItemGradeV2(
                    item_id=item.item_id,
                    criterion_grades=[
                        CriterionGradeV2(
                            criterion_id=criterion.criterion_id,
                            points_awarded=0,
                            evidence_quote="",
                            reason_code="missing",
                            feedback="No answer was submitted.",
                        )
                        for criterion in item.rubric
                    ],
                    wrong_reason_tags=["unanswered"],
                    confidence=1.0,
                    needs_human_review=False,
                    feedback="No answer was submitted.",
                )
            )
            continue
        if item.question_type == "choice":
            correct = answer.strip() == item.reference_answer
            grades.append(
                ItemGradeV2(
                    item_id=item.item_id,
                    criterion_grades=[
                        CriterionGradeV2(
                            criterion_id=criterion.criterion_id,
                            points_awarded=criterion.max_points if correct else 0,
                            evidence_quote=answer.strip() if correct else "",
                            reason_code="satisfied" if correct else "incorrect",
                            feedback="Correct option selected." if correct else "The selected option is not correct.",
                        )
                        for criterion in item.rubric
                    ],
                    wrong_reason_tags=[] if correct else ["incorrect_choice"],
                    confidence=1.0,
                    needs_human_review=False,
                    feedback="Correct." if correct else "Review the selected option.",
                )
            )
            continue

        lowered = answer.casefold()
        criterion_grades: list[CriterionGradeV2] = []
        reliable = False
        for criterion in item.rubric:
            signals = [signal.casefold() for signal in criterion.deterministic_signals if signal]
            matched = [signal for signal in signals if signal in lowered]
            if signals and matched:
                reliable = True
                criterion_grades.append(
                    CriterionGradeV2(
                        criterion_id=criterion.criterion_id,
                        points_awarded=criterion.max_points,
                        evidence_quote=matched[0],
                        reason_code="satisfied",
                        feedback="A deterministic rubric signal was present.",
                    )
                )
            else:
                criterion_grades.append(
                    CriterionGradeV2(
                        criterion_id=criterion.criterion_id,
                        points_awarded=0,
                        evidence_quote="",
                        reason_code="missing",
                        feedback="The answer could not be reliably scored by deterministic signals.",
                    )
                )
        grades.append(
            ItemGradeV2(
                item_id=item.item_id,
                criterion_grades=criterion_grades,
                wrong_reason_tags=[] if reliable else ["insufficient_deterministic_evidence"],
                confidence=0.55 if reliable else 0.0,
                needs_human_review=not reliable,
                feedback="Deterministic fallback score." if reliable else "This answer requires review.",
            )
        )
    result = AssessmentGradeBundleV2(
        schema_version="assessment-grade-v2",
        grader_version="assessment-grader-v2",
        item_grades=grades,
        overall_feedback="Assessment grading completed.",
    )
    validate_grade_bundle(context, result)
    return result


def validate_grade_bundle(context: AssessmentGradingContextV2, bundle: AssessmentGradeBundleV2) -> None:
    context_items = {item.item_id: item for item in context.items}
    grades = {grade.item_id: grade for grade in bundle.item_grades}
    if set(grades) != set(context_items) or len(grades) != len(bundle.item_grades):
        raise AssessmentDomainError("Grade bundle item IDs do not match the assessment.", code="assessment.grading_output_invalid")
    for item_id, item in context_items.items():
        grade = grades[item_id]
        criteria = {criterion.criterion_id: criterion for criterion in item.rubric}
        returned = {criterion.criterion_id: criterion for criterion in grade.criterion_grades}
        if set(criteria) != set(returned) or len(returned) != len(grade.criterion_grades):
            raise AssessmentDomainError("Grade bundle criterion IDs do not match the rubric.", code="assessment.grading_output_invalid")
        answer = context.submitted_answers.get(item_id, "")
        for criterion_id, item_grade in returned.items():
            if item_grade.points_awarded > criteria[criterion_id].max_points:
                raise AssessmentDomainError("Grade points exceed rubric maximum.", code="assessment.grading_output_invalid")
            if item_grade.evidence_quote not in answer:
                raise AssessmentDomainError("Grade evidence quote is not present in the submitted answer.", code="assessment.grading_output_invalid")


def score_item(grade: ItemGradeV2, context_item) -> float | None:
    if grade.needs_human_review:
        return None
    maximum = sum(criterion.max_points for criterion in context_item.rubric)
    if maximum == 0:
        return None
    return round(100 * sum(criterion.points_awarded for criterion in grade.criterion_grades) / maximum, 2)


def overall_score(bundle: AssessmentGradeBundleV2, context: AssessmentGradingContextV2) -> float | None:
    by_id = {item.item_id: item for item in context.items}
    values = [score_item(grade, by_id[grade.item_id]) for grade in bundle.item_grades]
    if any(value is None for value in values):
        return None
    return round(mean(value for value in values if value is not None), 2)
