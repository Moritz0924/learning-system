from __future__ import annotations

from statistics import mean
from uuid import uuid4

from .schemas import AssessmentAttemptResult, AssessmentDraft, AssessmentItem, AssessmentType, MasteryUpdate


ITEM_COUNTS = {"daily": 3, "weekly": 10, "phase": 4}


def build_assessment_draft(
    assessment_type: AssessmentType,
    knowledge_node_ids: list[str],
    *,
    source_chunk_ids: list[str] | None = None,
) -> AssessmentDraft:
    nodes = knowledge_node_ids or ["general_foundations"]
    source_ids = source_chunk_ids or []
    count = ITEM_COUNTS[assessment_type]
    items = []
    for index in range(count):
        question_type = ("choice", "explain", "code_reading")[index % 3]
        node_id = nodes[index % len(nodes)]
        items.append(
            AssessmentItem(
            item_id=f"item-{uuid4()}",
            knowledge_node_id=node_id,
            question_type=question_type,
            prompt=(f"Choose the best answer for {node_id}." if question_type == "choice" else f"Explain key idea {index + 1} for {node_id}."),
            options_json=(
                {"options": [{"option_id": "option-a", "label": "Use the documented safe approach."}, {"option_id": "option-b", "label": "Skip validation."}]}
                if question_type == "choice"
                else {}
            ),
            reference_answer=("option-a" if question_type == "choice" else f"A good answer mentions {node_id} and uses concrete reasoning."),
            rubric_json={"max_score": 100, "rule_version": "phase2-rubric-v1"},
            difficulty=2 + (index % 3),
            source_chunk_ids=source_ids,
            )
        )
    return AssessmentDraft(
        assessment_id=f"assessment-{uuid4()}",
        assessment_type=assessment_type,
        status="draft",
        scope={"knowledge_node_ids": nodes},
        items=items,
    )


def grade_assessment_attempt(
    draft: AssessmentDraft,
    answers: dict[str, str],
) -> AssessmentAttemptResult:
    answer_results = []
    for item in draft.items:
        answer = answers.get(item.item_id, "")
        is_blank = not answer.strip()
        score = 0 if is_blank else _score_answer(answer, item.reference_answer)
        wrong_tags = [] if score >= 70 else (["unanswered"] if is_blank else ["missing_key_concept"])
        answer_results.append(
            {
                "item_id": item.item_id,
                "answer_text": answer,
                "score": score,
                "grader_type": "rule",
                "grader_reason": "keyword and length based deterministic V1 rubric",
                "evidence_json": {
                    "rubric_version": "phase2-rubric-v1",
                    "answer_status": "blank" if is_blank else "answered",
                    "wrong_reason_tags": wrong_tags,
                },
            }
        )
    total = mean([item["score"] for item in answer_results]) if answer_results else 0
    return AssessmentAttemptResult(
        assessment_id=draft.assessment_id,
        attempt_id=f"attempt-{uuid4()}",
        score=round(total, 2),
        feedback="Review missing concepts." if total < 70 else "Good progress.",
        status="graded",
        answers=answer_results,
    )


def calculate_mastery_update(
    *,
    knowledge_node_id: str,
    previous_score: float | None,
    recent_assessment_score: float | None,
    explanation_score: float | None,
    task_independence_score: float | None,
    days_since_practice: int | None,
    evidence_count: int,
) -> MasteryUpdate:
    missing: dict[str, str] = {}
    confidence = 0.95
    previous = _default(previous_score, 60, missing, "previous_score")
    recent = _default(recent_assessment_score, 60, missing, "recent_assessment_score")
    explanation = _default(explanation_score, 60, missing, "explanation_score")
    independence = _default(task_independence_score, 60, missing, "task_independence_score")
    if days_since_practice is None:
        decay = 0
        missing["days_since_practice"] = "decay_skipped"
    else:
        decay = min(15, max(0, days_since_practice) * 0.6)
    if missing:
        confidence -= min(0.4, 0.1 * len(missing))
    raw = 0.55 * previous + 0.25 * recent + 0.10 * explanation + 0.10 * independence - decay
    return MasteryUpdate(
        knowledge_node_id=knowledge_node_id,
        previous_score=clamp(previous),
        new_score=clamp(raw),
        confidence=round(max(0.1, confidence), 2),
        evidence_count=evidence_count,
        calculation_version="phase2-mastery-v1",
        source_breakdown={
            "historical_mastery": previous,
            "recent_assessment": recent,
            "explanation_score": explanation,
            "task_independence": independence,
            "forgetting_decay": decay,
        },
        missing_data_strategy=missing,
    )


def mastery_updates_from_attempt(
    draft: AssessmentDraft,
    result: AssessmentAttemptResult,
    current_mastery: dict,
) -> list[MasteryUpdate]:
    updates = []
    for node_id in sorted({item.knowledge_node_id for item in draft.items}):
        node_scores = [
            answer.score
            for answer in result.answers
            for item in draft.items
            if item.item_id == answer.item_id and item.knowledge_node_id == node_id
        ]
        previous = current_mastery.get(node_id, {}).get("score", 60)
        updates.append(
            calculate_mastery_update(
                knowledge_node_id=node_id,
                previous_score=previous,
                recent_assessment_score=mean(node_scores) if node_scores else None,
                explanation_score=mean(node_scores) if node_scores else None,
                task_independence_score=70 if result.score >= 70 else 40,
                days_since_practice=0,
                evidence_count=len(node_scores),
            )
        )
    return updates


def _score_answer(answer: str, reference: str) -> float:
    normalized = answer.strip().lower()
    if not normalized or "not sure" in normalized or normalized == "wrong":
        return 35
    reference_terms = {term.strip(".,").lower() for term in reference.split() if len(term) > 4}
    matches = sum(1 for term in reference_terms if term in normalized)
    return clamp(55 + matches * 12 + min(len(normalized), 120) / 6)


def _default(value: float | None, default: float, missing: dict[str, str], key: str) -> float:
    if value is None:
        missing[key] = f"defaulted_to_{int(default)}"
        return default
    return value


def clamp(value: float) -> float:
    return round(max(0, min(100, value)), 2)
