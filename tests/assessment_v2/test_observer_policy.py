from __future__ import annotations

from backend.app.domain.assessment.contracts import ObserverSignalBundleV2
from backend.app.domain.assessment.observer_policy import decide_observer


def test_manual_review_overrides_all_automated_actions() -> None:
    decision = decide_observer(
        ObserverSignalBundleV2(
            phase_status="graded",
            readiness_score=95,
            mastery_score=95,
            mastery_confidence=0.95,
            completion_rate_7d=1,
            recent_task_count=10,
            low_prerequisite_count=0,
            valid_sessions=3,
            needs_human_review=True,
            has_reliable_evidence=True,
            automatic_adjustment_eligible=True,
        )
    )

    assert decision.decision == "manual_review"
    assert decision.automation_allowed is False


def test_advance_requires_phase_gate_and_two_valid_sessions() -> None:
    signal = ObserverSignalBundleV2(
        phase_status="graded",
        readiness_score=80,
        mastery_score=85,
        mastery_confidence=0.75,
        completion_rate_7d=0.85,
        recent_task_count=5,
        low_prerequisite_count=0,
        valid_sessions=2,
        has_reliable_evidence=True,
        automatic_adjustment_eligible=True,
    )

    assert decide_observer(signal).decision == "advance"
    assert decide_observer(signal.model_copy(update={"valid_sessions": 1})).decision == "keep"
