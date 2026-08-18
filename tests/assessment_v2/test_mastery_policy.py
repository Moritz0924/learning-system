from __future__ import annotations

from datetime import datetime, timezone

from backend.app.domain.assessment.contracts import MasteryEvidenceV2
from backend.app.domain.assessment.mastery_policy import calculate_mastery_updates


def _evidence(score: float, *, mode: str = "remote_structured", confidence: float = 1.0) -> MasteryEvidenceV2:
    return MasteryEvidenceV2(
        knowledge_node_id="node-1",
        assessment_id="assessment-1",
        attempt_id="attempt-1",
        item_id="item-1",
        question_type="explain",
        score=score,
        grader_confidence=confidence,
        grading_mode=mode,
        reliability_weight=0.9,
        eligible_for_mastery=mode != "manual_review_required",
        wrong_reason_tags=[] if score >= 70 else ["missing_core"],
        occurred_at=datetime.now(timezone.utc),
    )


def test_no_valid_evidence_keeps_mastery_score_and_disables_automation() -> None:
    result = calculate_mastery_updates(
        {"node-1": {"score": 60, "confidence": 0.7, "evidence_weight": 2}},
        [_evidence(90, mode="manual_review_required", confidence=0)],
    )[0]

    assert result.new_score == 60
    assert result.evidence_score is None
    assert result.automatic_adjustment_eligible is False


def test_reliable_evidence_uses_alpha_cap_and_bounds() -> None:
    result = calculate_mastery_updates(
        {"node-1": {"score": 50, "confidence": 0.5, "evidence_weight": 10}},
        [_evidence(100), _evidence(100)],
    )[0]

    assert 50 < result.new_score < 100
    assert result.new_confidence > 0.5
    assert result.total_evidence_weight > 1
    assert result.automatic_adjustment_eligible is True
