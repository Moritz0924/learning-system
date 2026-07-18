from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiagnosticOption(_StrictFrozenModel):
    option_id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class SelfAssessmentDimension(_StrictFrozenModel):
    code: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    minimum: int = 0
    maximum: int = 4
    related_node_codes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_range_and_nodes(self) -> "SelfAssessmentDimension":
        if self.minimum >= self.maximum:
            raise ValueError("self-assessment minimum must be less than maximum")
        if len(set(self.related_node_codes)) != len(self.related_node_codes):
            raise ValueError("duplicate related node code")
        return self


class DiagnosticQuestionDefinition(_StrictFrozenModel):
    question_id: str = Field(min_length=1)
    node_code: str = Field(min_length=1)
    question_type: Literal["single_choice"]
    prompt: str = Field(min_length=1)
    options: tuple[DiagnosticOption, ...] = Field(min_length=2)
    correct_option_id: str = Field(min_length=1)
    weight: float = Field(gt=0, allow_inf_nan=False)
    difficulty: int = Field(ge=1, le=5)

    @model_validator(mode="after")
    def validate_options(self) -> "DiagnosticQuestionDefinition":
        option_ids = [option.option_id for option in self.options]
        if len(set(option_ids)) != len(option_ids):
            raise ValueError("duplicate option id")
        if self.correct_option_id not in option_ids:
            raise ValueError("correct option must reference an option in the question")
        return self


class DiagnosticTemplate(_StrictFrozenModel):
    template_version: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    title: str = Field(min_length=1)
    self_assessment_dimensions: tuple[SelfAssessmentDimension, ...] = Field(min_length=1)
    questions: tuple[DiagnosticQuestionDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_identifiers(self) -> "DiagnosticTemplate":
        dimension_codes = [dimension.code for dimension in self.self_assessment_dimensions]
        if len(set(dimension_codes)) != len(dimension_codes):
            raise ValueError("duplicate self-assessment dimension code")

        question_ids = [question.question_id for question in self.questions]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("duplicate diagnostic question id")
        return self


class SelfAssessmentAnswer(_StrictFrozenModel):
    dimension_code: str = Field(min_length=1)
    level: int


class DiagnosticKnowledgeAnswer(_StrictFrozenModel):
    question_id: str = Field(min_length=1)
    selected_option_id: str = Field(min_length=1)


class CurriculumNodeDefinition(_StrictFrozenModel):
    node_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    sequence: int
    mastery_threshold: float = Field(ge=0, le=100, allow_inf_nan=False)


class NodeMasteryScore(_StrictFrozenModel):
    knowledge_node_id: str
    node_code: str
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    self_score: float | None = Field(default=None, ge=0, le=100)
    objective_score: float | None = Field(default=None, ge=0, le=100)


class KnowledgeGap(_StrictFrozenModel):
    node_id: str
    node_code: str
    score: float = Field(ge=0, le=100)
    mastery_threshold: float = Field(ge=0, le=100)


class DiagnosisScoringResult(_StrictFrozenModel):
    entry_node_id: str
    entry_node_code: str
    initial_mastery: dict[str, NodeMasteryScore]
    knowledge_gaps: tuple[KnowledgeGap, ...]
    all_baseline_nodes_passed: bool


class PublicSelfAssessmentDimension(_StrictFrozenModel):
    code: str
    title: str
    description: str
    minimum: int
    maximum: int


class PublicDiagnosticQuestion(_StrictFrozenModel):
    question_id: str
    node_code: str
    question_type: Literal["single_choice"]
    prompt: str
    options: tuple[DiagnosticOption, ...]


class PublicDiagnosticTemplate(_StrictFrozenModel):
    template_version: str
    domain: str
    title: str
    self_assessment_dimensions: tuple[PublicSelfAssessmentDimension, ...]
    questions: tuple[PublicDiagnosticQuestion, ...]


def public_template(template: DiagnosticTemplate) -> PublicDiagnosticTemplate:
    """Project an internal scoring template into its safe client-facing contract."""

    return PublicDiagnosticTemplate(
        template_version=template.template_version,
        domain=template.domain,
        title=template.title,
        self_assessment_dimensions=tuple(
            PublicSelfAssessmentDimension(
                code=dimension.code,
                title=dimension.title,
                description=dimension.description,
                minimum=dimension.minimum,
                maximum=dimension.maximum,
            )
            for dimension in template.self_assessment_dimensions
        ),
        questions=tuple(
            PublicDiagnosticQuestion(
                question_id=question.question_id,
                node_code=question.node_code,
                question_type=question.question_type,
                prompt=question.prompt,
                options=question.options,
            )
            for question in template.questions
        ),
    )
