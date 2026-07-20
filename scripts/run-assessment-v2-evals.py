from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from backend.app.application.assessment_context_service import canonical_json_hash
from backend.app.domain.assessment.contracts import (
    AssessmentGenerationContextV2,
    AssessmentGenerationPolicy,
    AssessmentGoalContext,
    AssessmentGradingContextV2,
    AssessmentItemForGrading,
    AssessmentKnowledgeNodeContext,
    GeneratedOptionV2,
    MasteryEvidenceV2,
    ObserverSignalBundleV2,
    RubricCriterionV2,
)
from backend.app.domain.assessment.generation_policy import BlueprintRegistry, deterministic_generation, validate_generation_bundle
from backend.app.domain.assessment.grading_policy import deterministic_grade, score_item
from backend.app.domain.assessment.mastery_policy import calculate_mastery_updates
from backend.app.domain.assessment.observer_policy import decide_observer


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "assessment_v2"
FORBIDDEN_FIXTURE_TOKENS = {"api_key", "secret", "system_prompt", "chain_of_thought"}


def _rows(name: str) -> list[dict]:
    path = FIXTURES / name
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise AssertionError(f"{path} must include at least one fixture")
    for row in rows:
        serialized = json.dumps(row, sort_keys=True).lower()
        if any(token in serialized for token in FORBIDDEN_FIXTURE_TOKENS):
            raise AssertionError(f"{path} contains non-sanitized fixture data")
    return rows


def _generation_context(node_code: str, assessment_type: str) -> AssessmentGenerationContextV2:
    payload = {
        "schema_version": "assessment-generation-context-v2",
        "user_id": "evaluation-user",
        "goal_id": "evaluation-goal",
        "assessment_type": assessment_type,
        "requested_item_count": {"daily": 3, "weekly": 10, "phase": 4}[assessment_type],
        "requested_knowledge_node_ids": ["node-evaluation"],
        "goal": AssessmentGoalContext(title="Evaluation goal", target_outcome="Validate V2 assessment contracts.").model_dump(mode="json"),
        "current_task": None,
        "knowledge_nodes": [
            AssessmentKnowledgeNodeContext(
                knowledge_node_id="node-evaluation",
                code=node_code,
                title=node_code.replace("_", " ").title(),
                learning_objectives=["Apply the skill safely."],
                difficulty=2,
                mastery_threshold=70,
            ).model_dump(mode="json")
        ],
        "mastery": [],
        "recent_misconceptions": [],
        "recent_attempt_summaries": [],
        "source_excerpts": [],
        "generation_policy": AssessmentGenerationPolicy().model_dump(mode="json"),
    }
    return AssessmentGenerationContextV2(**payload, context_hash=canonical_json_hash(payload))


def _grading_context(question_type: str, answer: str, reference_answer: str) -> tuple[AssessmentGradingContextV2, AssessmentItemForGrading]:
    item = AssessmentItemForGrading(
        item_id="item-evaluation",
        knowledge_node_id="node-evaluation",
        question_type=question_type,
        prompt="Provide a safe assessment answer.",
        options=[GeneratedOptionV2(option_key="option-a", label="Stable UUID"), GeneratedOptionV2(option_key="option-b", label="New UUID")],
        reference_answer=reference_answer,
        rubric=[RubricCriterionV2(criterion_id="criterion-1", description="Correct response", max_points=100)],
        difficulty=2,
    )
    payload = {
        "schema_version": "assessment-grading-context-v2",
        "assessment_id": "assessment-evaluation",
        "attempt_id": "attempt-evaluation",
        "assessment_type": "daily",
        "items": [item.model_dump(mode="json")],
        "submitted_answers": {item.item_id: answer},
        "grading_policy_version": "assessment-grading-policy-v2",
    }
    return AssessmentGradingContextV2(**payload, context_hash=canonical_json_hash(payload)), item


def evaluate_generation() -> int:
    count = 0
    for row in _rows("generation.jsonl"):
        context = _generation_context(row["node_code"], row["assessment_type"])
        bundle = deterministic_generation(context, BlueprintRegistry.default())
        validate_generation_bundle(context, bundle)
        assert len(bundle.items) == row["expected_items"]
        count += 1
    return count


def evaluate_grading() -> int:
    count = 0
    for row in _rows("grading.jsonl"):
        context, item = _grading_context(row["question_type"], row["answer"], row["reference_answer"])
        bundle = deterministic_grade(context)
        assert score_item(bundle.item_grades[0], item) == row["expected_score"]
        count += 1
    return count


def evaluate_mastery() -> int:
    count = 0
    for row in _rows("mastery.jsonl"):
        mode = row.get("grading_mode", "remote_structured")
        previous = {"node-evaluation": {"score": row["previous_score"], "confidence": row["previous_confidence"]}}
        evidence = MasteryEvidenceV2(
            knowledge_node_id="node-evaluation",
            assessment_id="assessment-evaluation",
            attempt_id="attempt-evaluation",
            item_id=row["case_id"],
            question_type="explain",
            score=row["evidence_score"],
            grader_confidence=1 if mode != "manual_review_required" else 0,
            grading_mode=mode,
            reliability_weight=0.9,
            eligible_for_mastery=mode != "manual_review_required",
            occurred_at=datetime.now(timezone.utc),
        )
        update = calculate_mastery_updates(previous, [evidence])[0]
        if row["expected_direction"] == "increase":
            assert update.new_score > row["previous_score"]
        else:
            assert update.new_score == row["previous_score"]
        count += 1
    return count


def evaluate_observer() -> int:
    count = 0
    for row in _rows("observer.jsonl"):
        signal_values = {key: value for key, value in row.items() if key not in {"case_id", "expected_decision"}}
        signal_values.setdefault("mastery_confidence", 0)
        signal_values.setdefault("recent_task_count", 0)
        signal_values.setdefault("low_prerequisite_count", 0)
        signal_values.setdefault("valid_sessions", 0)
        decision = decide_observer(ObserverSignalBundleV2(**signal_values))
        assert decision.decision == row["expected_decision"]
        count += 1
    return count


def main() -> None:
    counts = {
        "generation": evaluate_generation(),
        "grading": evaluate_grading(),
        "mastery": evaluate_mastery(),
        "observer": evaluate_observer(),
    }
    if os.getenv("ASSESSMENT_REMOTE_EVAL_ENABLED", "").lower() in {"1", "true", "yes"}:
        # Remote evaluation remains opt-in. The same sanitized, human-labelled
        # grading fixtures are the only inputs eligible for a provider run.
        _rows("grading.jsonl")
        print("assessment-v2 remote evaluation: sanitized fixtures verified")
    print(f"assessment-v2 deterministic evaluation: passed ({counts})")


if __name__ == "__main__":
    main()
