from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from .contracts import (
    CurriculumNodeDefinition,
    DiagnosisScoringResult,
    DiagnosticKnowledgeAnswer,
    DiagnosticTemplate,
    KnowledgeGap,
    NodeMasteryScore,
    SelfAssessmentAnswer,
)
from .validation import DiagnosisValidationError, validate_diagnostic_answers


def _rounded(value: float) -> float:
    return round(value, 2)


def score_diagnosis(
    *,
    template: DiagnosticTemplate,
    self_answers: Sequence[SelfAssessmentAnswer],
    knowledge_answers: Sequence[DiagnosticKnowledgeAnswer],
    curriculum_nodes: Sequence[CurriculumNodeDefinition],
) -> DiagnosisScoringResult:
    """Score one template submission without database, LLM, or environment access."""

    validated = validate_diagnostic_answers(
        template=template,
        template_version=template.template_version,
        self_answers=self_answers,
        knowledge_answers=knowledge_answers,
    )

    nodes_by_code: dict[str, CurriculumNodeDefinition] = {}
    for node in curriculum_nodes:
        if node.code in nodes_by_code:
            raise DiagnosisValidationError(
                "duplicate_curriculum_node", f"Duplicate curriculum node code: {node.code}"
            )
        nodes_by_code[node.code] = node

    template_node_codes = {
        node_code
        for dimension in template.self_assessment_dimensions
        for node_code in dimension.related_node_codes
    } | {question.node_code for question in template.questions}
    missing_node_codes = sorted(template_node_codes - nodes_by_code.keys())
    if missing_node_codes:
        raise DiagnosisValidationError(
            "curriculum_node_not_found",
            "Template references unknown curriculum nodes: " + ", ".join(missing_node_codes),
        )

    self_scores_by_node: dict[str, list[float]] = defaultdict(list)
    dimensions_by_code = {
        dimension.code: dimension for dimension in template.self_assessment_dimensions
    }
    for dimension_code, value in validated.self_answers_by_dimension.items():
        dimension = dimensions_by_code[dimension_code]
        normalized = 100 * (value - dimension.minimum) / (dimension.maximum - dimension.minimum)
        for node_code in dimension.related_node_codes:
            self_scores_by_node[node_code].append(normalized)

    objective_weight_by_node: dict[str, float] = defaultdict(float)
    correct_weight_by_node: dict[str, float] = defaultdict(float)
    for question in template.questions:
        selected_option_id = validated.knowledge_answers_by_question[question.question_id]
        objective_weight_by_node[question.node_code] += question.weight
        if selected_option_id == question.correct_option_id:
            correct_weight_by_node[question.node_code] += question.weight

    mastery: dict[str, NodeMasteryScore] = {}
    ordered_supported_nodes = sorted(
        (nodes_by_code[code] for code in template_node_codes),
        key=lambda node: (node.sequence, node.code),
    )
    for node in ordered_supported_nodes:
        self_values = self_scores_by_node.get(node.code, [])
        self_score = _rounded(sum(self_values) / len(self_values)) if self_values else None
        objective_weight = objective_weight_by_node.get(node.code, 0)
        objective_score = (
            _rounded(100 * correct_weight_by_node[node.code] / objective_weight)
            if objective_weight
            else None
        )

        if self_score is not None and objective_score is not None:
            score = _rounded(0.25 * self_score + 0.75 * objective_score)
            confidence = 0.85
        elif objective_score is not None:
            score = objective_score
            confidence = 0.70
        elif self_score is not None:
            score = self_score
            confidence = 0.40
        else:
            continue

        mastery[node.code] = NodeMasteryScore(
            knowledge_node_id=node.node_id,
            node_code=node.code,
            score=score,
            confidence=confidence,
            self_score=self_score,
            objective_score=objective_score,
        )

    if not mastery:
        raise DiagnosisValidationError(
            "no_scorable_curriculum_nodes", "Diagnostic submission produced no scorable curriculum nodes."
        )

    scored_nodes = [node for node in ordered_supported_nodes if node.code in mastery]
    gaps = tuple(
        sorted(
            (
                KnowledgeGap(
                    node_id=node.node_id,
                    node_code=node.code,
                    score=mastery[node.code].score,
                    mastery_threshold=node.mastery_threshold,
                )
                for node in scored_nodes
                if mastery[node.code].score < node.mastery_threshold
            ),
            key=lambda gap: (
                nodes_by_code[gap.node_code].sequence,
                gap.score,
                gap.node_code,
            ),
        )
    )

    if gaps:
        entry_node = nodes_by_code[gaps[0].node_code]
        all_baseline_nodes_passed = False
    else:
        entry_node = scored_nodes[-1]
        all_baseline_nodes_passed = True

    return DiagnosisScoringResult(
        entry_node_id=entry_node.node_id,
        entry_node_code=entry_node.code,
        initial_mastery=mastery,
        knowledge_gaps=gaps,
        all_baseline_nodes_passed=all_baseline_nodes_passed,
    )
