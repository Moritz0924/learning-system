from __future__ import annotations

from datetime import timedelta

from adaptive_tutor.phase2.schemas import AssessmentAttemptResult, MasteryUpdate

from backend.app.application.memory_candidate_service import (
    build_explicit_goal_candidate,
    build_explicit_preference_candidate,
    build_learning_result_candidates,
    generated_memory_idempotency_key,
)

from .helpers import FIXED_NOW


def _update(*, previous_score: float, new_score: float, confidence: float = 0.8) -> MasteryUpdate:
    return MasteryUpdate(
        knowledge_node_id="python-basics",
        previous_score=previous_score,
        new_score=new_score,
        confidence=confidence,
        evidence_count=3,
        calculation_version="mastery-v1",
        source_breakdown={"assessment": 1.0},
        missing_data_strategy={},
    )


def _attempt() -> AssessmentAttemptResult:
    return AssessmentAttemptResult(
        assessment_id="assessment-1",
        attempt_id="attempt-1",
        score=85,
        feedback="good",
        answers=[],
    )


def test_explicit_candidates_use_request_scoped_idempotency_and_server_owned_fields() -> None:
    preference = build_explicit_preference_candidate(
        user_id="user-1",
        request_id="00000000-0000-4000-8000-000000000001",
        preference_key="explanation_style",
        preference_value="examples_first",
    )
    goal = build_explicit_goal_candidate(
        user_id="user-1",
        goal_id="goal-1",
        request_id="00000000-0000-4000-8000-000000000002",
        title="Ship a tutor",
        target_outcome="Deploy a reliable learning system",
        deadline="2026-12-31",
    )

    assert preference.command.idempotency_key == "memory-v1:explicit:00000000-0000-4000-8000-000000000001"
    assert preference.command.goal_id is None
    assert preference.command.source_kind == "explicit_user"
    assert preference.command.importance == 0.5
    assert preference.command.confidence == 1.0
    assert goal.command.goal_id == "goal-1"
    assert goal.command.content["deadline"] == "2026-12-31"
    assert goal.origin == "explicit_user_statement"


def test_generated_idempotency_key_is_stable_and_changes_with_semantic_identity() -> None:
    first = generated_memory_idempotency_key(
        source_ref_id="attempt-1",
        memory_type="mastery_summary",
        semantic_key="mastery:goal-1:node-1",
    )
    same = generated_memory_idempotency_key(
        source_ref_id="attempt-1",
        memory_type="mastery_summary",
        semantic_key="mastery:goal-1:node-1",
    )
    changed = generated_memory_idempotency_key(
        source_ref_id="attempt-1",
        memory_type="mastery_summary",
        semantic_key="mastery:goal-1:node-2",
    )

    assert first == same
    assert first != changed
    assert first.startswith("memory-v1:generated:")
    assert len(first) <= 160


def test_every_mastery_update_generates_30_day_summary_with_fixed_importance() -> None:
    candidates = build_learning_result_candidates(
        user_id="user-1",
        goal_id="goal-1",
        assessment_result=_attempt(),
        mastery_updates=[_update(previous_score=60, new_score=79)],
        now=FIXED_NOW,
    )

    assert len(candidates) == 1
    command = candidates[0].command
    assert command.memory_type == "mastery_summary"
    assert command.source_kind == "assessment"
    assert command.source_ref_id == "attempt-1"
    assert command.importance == 0.8
    assert command.confidence == 0.8
    assert command.expires_at == FIXED_NOW + timedelta(days=30)
    assert command.content == {
        "knowledge_node_id": "python-basics",
        "score": 79.0,
        "confidence": 0.8,
        "evidence_count": 3,
        "calculation_version": "mastery-v1",
    }


def test_crossing_80_with_confidence_generates_permanent_milestone() -> None:
    candidates = build_learning_result_candidates(
        user_id="user-1",
        goal_id="goal-1",
        assessment_result=_attempt(),
        mastery_updates=[_update(previous_score=79, new_score=80, confidence=0.7)],
        now=FIXED_NOW,
    )

    assert [candidate.command.memory_type for candidate in candidates] == [
        "mastery_summary",
        "learning_milestone",
    ]
    milestone = candidates[1].command
    assert milestone.expires_at is None
    assert milestone.importance == 0.8
    assert milestone.confidence == 0.7
    assert milestone.content["achieved_at"] == FIXED_NOW
    assert milestone.content["evidence_refs"] == ["attempt-1"]


def test_milestone_is_not_generated_without_threshold_crossing_or_confidence() -> None:
    candidates = build_learning_result_candidates(
        user_id="user-1",
        goal_id="goal-1",
        assessment_result=_attempt(),
        mastery_updates=[
            _update(previous_score=80, new_score=90),
            _update(previous_score=79, new_score=80, confidence=0.69).model_copy(
                update={"knowledge_node_id": "low-confidence-node"}
            ),
        ],
        now=FIXED_NOW,
    )

    assert [candidate.command.memory_type for candidate in candidates] == [
        "mastery_summary",
        "mastery_summary",
    ]
