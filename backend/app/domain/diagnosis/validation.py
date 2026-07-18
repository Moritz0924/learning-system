from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from .contracts import DiagnosticKnowledgeAnswer, DiagnosticTemplate, SelfAssessmentAnswer


class DiagnosisValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidatedDiagnosticAnswers:
    self_answers_by_dimension: Mapping[str, int]
    knowledge_answers_by_question: Mapping[str, str]


def validate_diagnostic_answers(
    *,
    template: DiagnosticTemplate,
    template_version: str,
    self_answers: Sequence[SelfAssessmentAnswer],
    knowledge_answers: Sequence[DiagnosticKnowledgeAnswer],
) -> ValidatedDiagnosticAnswers:
    if template_version != template.template_version:
        raise DiagnosisValidationError(
            "template_version_mismatch",
            "Submitted template version does not match the loaded diagnostic template.",
        )

    dimensions = {dimension.code: dimension for dimension in template.self_assessment_dimensions}
    self_by_dimension: dict[str, int] = {}
    for answer in self_answers:
        dimension = dimensions.get(answer.dimension_code)
        if dimension is None:
            raise DiagnosisValidationError(
                "unknown_self_assessment_dimension",
                f"Unknown self-assessment dimension: {answer.dimension_code}",
            )
        if answer.dimension_code in self_by_dimension:
            raise DiagnosisValidationError(
                "duplicate_self_assessment_answer",
                f"Duplicate self-assessment answer: {answer.dimension_code}",
            )
        if not dimension.minimum <= answer.level <= dimension.maximum:
            raise DiagnosisValidationError(
                "self_assessment_out_of_range",
                f"Self-assessment value for {answer.dimension_code} is outside the allowed range.",
            )
        self_by_dimension[answer.dimension_code] = answer.level

    questions = {question.question_id: question for question in template.questions}
    knowledge_by_question: dict[str, str] = {}
    for answer in knowledge_answers:
        question = questions.get(answer.question_id)
        if question is None:
            raise DiagnosisValidationError(
                "unknown_diagnostic_question",
                f"Unknown diagnostic question: {answer.question_id}",
            )
        if answer.question_id in knowledge_by_question:
            raise DiagnosisValidationError(
                "duplicate_knowledge_answer",
                f"Duplicate knowledge answer: {answer.question_id}",
            )
        if answer.selected_option_id not in {option.option_id for option in question.options}:
            raise DiagnosisValidationError(
                "invalid_diagnostic_option",
                f"Invalid option for diagnostic question: {answer.question_id}",
            )
        knowledge_by_question[answer.question_id] = answer.selected_option_id

    missing_question_ids = [
        question.question_id
        for question in template.questions
        if question.question_id not in knowledge_by_question
    ]
    if missing_question_ids:
        raise DiagnosisValidationError(
            "missing_required_knowledge_answer",
            "Missing required diagnostic answers: " + ", ".join(missing_question_ids),
        )

    return ValidatedDiagnosticAnswers(
        self_answers_by_dimension=MappingProxyType(self_by_dimension),
        knowledge_answers_by_question=MappingProxyType(knowledge_by_question),
    )
