from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.services.curriculum import ordered_nodes


SELF_KEYS = {
    "python_foundations": "python_level",
    "fastapi_basics": "api_level",
    "llm_api_basics": "llm_level",
    "rag_foundations": "rag_level",
    "langgraph_basics": "langgraph_level",
}


@dataclass(frozen=True)
class BaselineDiagnosisResult:
    entry_node_id: str
    entry_node_code: str
    knowledge_gaps: list[dict]
    initial_mastery: dict
    evidence_json: dict
    baseline_summary: str


def build_baseline_diagnosis(
    session: Session,
    *,
    curriculum_id: str,
    self_assessment: dict,
    submitted_answers: dict,
) -> BaselineDiagnosisResult:
    answers = {
        item.get("node_code"): bool(item.get("is_correct"))
        for item in submitted_answers.get("questions", [])
    }
    mastery: dict[str, dict] = {}
    gaps: list[dict] = []
    entry_node_id = ""
    entry_node_code = ""

    for node in ordered_nodes(session, curriculum_id):
        level = int(self_assessment.get(SELF_KEYS[node.code], 0))
        score = min(100, 35 + level * 10)
        if answers.get(node.code) is True:
            score = max(score, 78)
        if answers.get(node.code) is False:
            score = min(score, 58)

        mastery[node.code] = {
            "knowledge_node_id": node.id,
            "score": score,
            "confidence": 0.75 if node.code in answers else 0.55,
        }
        if score < 70:
            gaps.append({"node_id": node.id, "node_code": node.code, "score": score})
            if not entry_node_code:
                entry_node_id = node.id
                entry_node_code = node.code

    if not entry_node_code:
        last = ordered_nodes(session, curriculum_id)[-1]
        entry_node_id = last.id
        entry_node_code = last.code

    return BaselineDiagnosisResult(
        entry_node_id=entry_node_id,
        entry_node_code=entry_node_code,
        knowledge_gaps=gaps,
        initial_mastery=mastery,
        evidence_json={
            "rule_version": "stage1-diagnosis-v1",
            "self_assessment": self_assessment,
            "submitted_answers": submitted_answers,
        },
        baseline_summary=f"Start at {entry_node_code}.",
    )
