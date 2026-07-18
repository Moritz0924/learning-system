from __future__ import annotations

import pytest

from backend.app.domain.diagnosis.contracts import (
    CurriculumNodeDefinition,
    DiagnosticKnowledgeAnswer,
    SelfAssessmentAnswer,
)
from backend.app.domain.diagnosis.scoring import score_diagnosis
from backend.app.domain.diagnosis.validation import DiagnosisValidationError


def _nodes() -> list[CurriculumNodeDefinition]:
    return [
        CurriculumNodeDefinition(
            node_id="node-python", code="python_foundations", sequence=1, mastery_threshold=70
        ),
        CurriculumNodeDefinition(
            node_id="node-api", code="fastapi_basics", sequence=2, mastery_threshold=70
        ),
        CurriculumNodeDefinition(
            node_id="node-rag", code="rag_foundations", sequence=3, mastery_threshold=70
        ),
        CurriculumNodeDefinition(
            node_id="node-langgraph", code="langgraph_basics", sequence=4, mastery_threshold=70
        ),
    ]


def _knowledge_answers(*, python_1: str = "b", python_2: str = "a", api: str = "a"):
    return [
        DiagnosticKnowledgeAnswer(question_id="python-1", selected_option_id=python_1),
        DiagnosticKnowledgeAnswer(question_id="python-2", selected_option_id=python_2),
        DiagnosticKnowledgeAnswer(question_id="api-1", selected_option_id=api),
    ]


def test_scoring_combines_weighted_objective_and_self_scores(diagnostic_template) -> None:
    result = score_diagnosis(
        template=diagnostic_template,
        self_answers=[SelfAssessmentAnswer(dimension_code="python", level=3)],
        knowledge_answers=_knowledge_answers(python_1="b", python_2="b"),
        curriculum_nodes=_nodes(),
    )

    python = result.initial_mastery["python_foundations"]
    assert python.self_score == 75
    assert python.objective_score == 25
    assert python.score == 37.5
    assert python.confidence == 0.85

    api = result.initial_mastery["fastapi_basics"]
    assert api.self_score is None
    assert api.objective_score == 100
    assert api.score == 100
    assert api.confidence == 0.70


def test_scoring_supports_self_only_nodes_and_omits_nodes_without_evidence(diagnostic_template) -> None:
    result = score_diagnosis(
        template=diagnostic_template,
        self_answers=[SelfAssessmentAnswer(dimension_code="rag", level=2)],
        knowledge_answers=_knowledge_answers(),
        curriculum_nodes=_nodes(),
    )

    rag = result.initial_mastery["rag_foundations"]
    assert rag.self_score == 50
    assert rag.objective_score is None
    assert rag.score == 50
    assert rag.confidence == 0.40
    assert "langgraph_basics" not in result.initial_mastery


@pytest.mark.parametrize(
    ("answers", "expected_python", "expected_api"),
    [
        (_knowledge_answers(), 100, 100),
        (_knowledge_answers(python_1="a", python_2="b", api="b"), 0, 0),
    ],
)
def test_objective_scoring_handles_all_correct_and_all_wrong(
    diagnostic_template, answers, expected_python: float, expected_api: float
) -> None:
    result = score_diagnosis(
        template=diagnostic_template,
        self_answers=[],
        knowledge_answers=answers,
        curriculum_nodes=_nodes(),
    )

    assert result.initial_mastery["python_foundations"].score == expected_python
    assert result.initial_mastery["fastapi_basics"].score == expected_api


def test_scoring_rejects_unanswered_required_question(diagnostic_template) -> None:
    with pytest.raises(DiagnosisValidationError) as exc_info:
        score_diagnosis(
            template=diagnostic_template,
            self_answers=[],
            knowledge_answers=_knowledge_answers()[:-1],
            curriculum_nodes=_nodes(),
        )

    assert exc_info.value.code == "missing_required_knowledge_answer"
