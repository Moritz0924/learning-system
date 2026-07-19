from __future__ import annotations

from backend.app.domain.diagnosis.contracts import (
    CurriculumNodeDefinition,
    DiagnosticKnowledgeAnswer,
    SelfAssessmentAnswer,
)
from backend.app.domain.diagnosis.scoring import score_diagnosis


def _answers(*, python_1: str, python_2: str, api: str):
    return [
        DiagnosticKnowledgeAnswer(question_id="python-1", selected_option_id=python_1),
        DiagnosticKnowledgeAnswer(question_id="python-2", selected_option_id=python_2),
        DiagnosticKnowledgeAnswer(question_id="api-1", selected_option_id=api),
    ]


def _nodes(*, python_threshold: float = 70) -> list[CurriculumNodeDefinition]:
    return [
        CurriculumNodeDefinition(
            node_id="node-python",
            code="python_foundations",
            sequence=20,
            mastery_threshold=python_threshold,
        ),
        CurriculumNodeDefinition(
            node_id="node-api", code="fastapi_basics", sequence=30, mastery_threshold=70
        ),
        CurriculumNodeDefinition(
            node_id="node-rag", code="rag_foundations", sequence=40, mastery_threshold=70
        ),
        CurriculumNodeDefinition(
            node_id="node-unsupported", code="unsupported", sequence=50, mastery_threshold=70
        ),
    ]


def test_entry_node_is_first_below_its_curriculum_threshold(diagnostic_template) -> None:
    result = score_diagnosis(
        template=diagnostic_template,
        self_answers=[],
        knowledge_answers=_answers(python_1="b", python_2="a", api="b"),
        curriculum_nodes=list(reversed(_nodes())),
    )

    assert result.entry_node_id == "node-api"
    assert result.entry_node_code == "fastapi_basics"
    assert result.all_baseline_nodes_passed is False
    assert [gap.node_code for gap in result.knowledge_gaps] == ["fastapi_basics"]


def test_score_equal_to_threshold_is_mastered(diagnostic_template) -> None:
    result = score_diagnosis(
        template=diagnostic_template,
        self_answers=[SelfAssessmentAnswer(dimension_code="python", level=4)],
        knowledge_answers=_answers(python_1="b", python_2="b", api="a"),
        curriculum_nodes=_nodes(python_threshold=43.75),
    )

    assert result.initial_mastery["python_foundations"].score == 43.75
    assert result.entry_node_code != "python_foundations"


def test_all_passed_selects_last_supported_node_not_unscored_curriculum_tail(diagnostic_template) -> None:
    result = score_diagnosis(
        template=diagnostic_template,
        self_answers=[SelfAssessmentAnswer(dimension_code="rag", level=4)],
        knowledge_answers=_answers(python_1="b", python_2="a", api="a"),
        curriculum_nodes=_nodes(),
    )

    assert result.all_baseline_nodes_passed is True
    assert result.knowledge_gaps == ()
    assert result.entry_node_id == "node-rag"
    assert result.entry_node_code == "rag_foundations"


def test_knowledge_gaps_are_sorted_by_sequence_then_score(diagnostic_template) -> None:
    nodes = _nodes()
    nodes[0] = nodes[0].model_copy(update={"sequence": 30})
    nodes[1] = nodes[1].model_copy(update={"sequence": 20})

    result = score_diagnosis(
        template=diagnostic_template,
        self_answers=[],
        knowledge_answers=_answers(python_1="a", python_2="b", api="b"),
        curriculum_nodes=nodes,
    )

    assert [gap.node_code for gap in result.knowledge_gaps] == [
        "fastapi_basics",
        "python_foundations",
    ]
