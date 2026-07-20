from __future__ import annotations

from datetime import datetime, timezone

from adaptive_tutor.phase2.schemas import AssessmentAttemptResult, MasteryUpdate

from backend.app.application.memory_candidate_service import build_learning_result_candidates
from backend.app.domain.memory import (
    MemoryCandidate,
    MemoryDecision,
    MemoryPrivacySettings,
    evaluate_memory_candidates,
)


def decide_memory_candidates(
    *,
    user_id: str,
    goal_id: str,
    explicit_candidates: list[MemoryCandidate],
    assessment_result: AssessmentAttemptResult | None,
    mastery_updates: list[MasteryUpdate],
    privacy_settings: MemoryPrivacySettings,
) -> list[MemoryDecision]:
    now = datetime.now(timezone.utc)
    candidates = list(explicit_candidates)
    if assessment_result is not None and mastery_updates:
        candidates.extend(
            build_learning_result_candidates(
                user_id=user_id,
                goal_id=goal_id,
                assessment_result=assessment_result,
                mastery_updates=mastery_updates,
                now=now,
            )
        )
    return evaluate_memory_candidates(
        candidates,
        settings=privacy_settings,
        expected_user_id=user_id,
        expected_goal_id=goal_id,
        now=now,
    )
