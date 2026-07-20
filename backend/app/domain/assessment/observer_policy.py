from __future__ import annotations

from .contracts import ObserverDecisionV2, ObserverSignalBundleV2


def decide_observer(signals: ObserverSignalBundleV2) -> ObserverDecisionV2:
    summary = signals.model_dump(mode="json")
    if signals.needs_human_review or not signals.has_reliable_evidence or not signals.automatic_adjustment_eligible:
        return _decision("manual_review", False, signals.mastery_confidence, summary, ["insufficient_reliable_evidence"], "Reliable evidence is not sufficient for an automatic plan change.")
    if (
        (signals.mastery_score is not None and signals.mastery_score < 60)
        or signals.repeated_misconceptions
        or (signals.phase_status == "graded" and (signals.readiness_score or 0) < 70 and signals.mastery_confidence >= 0.65)
    ):
        return _decision("remediate", True, signals.mastery_confidence, summary, ["mastery_gap"], "Add targeted review work before progressing.")
    if (
        signals.phase_status == "graded"
        and (signals.readiness_score or 0) >= 80
        and signals.mastery_confidence >= 0.75
        and (signals.completion_rate_7d or 0) >= 0.85
        and signals.low_prerequisite_count == 0
        and signals.valid_sessions >= 2
    ):
        return _decision("advance", True, signals.mastery_confidence, summary, ["phase_gate_satisfied"], "Reliable assessment evidence supports advancing to the next learning step.")
    if signals.recent_task_count >= 5 and (signals.completion_rate_7d or 1) < 0.60:
        return _decision("reduce", True, signals.mastery_confidence, summary, ["sustained_low_completion"], "Recent completion data supports reducing planned workload.")
    return _decision("keep", False, signals.mastery_confidence, summary, ["no_change_required"], "The current plan remains appropriate for the available evidence.")


def _decision(decision: str, automation_allowed: bool, confidence: float, summary: dict, codes: list[str], rationale: str) -> ObserverDecisionV2:
    return ObserverDecisionV2(
        decision=decision,  # type: ignore[arg-type]
        automation_allowed=automation_allowed,
        confidence=confidence,
        evidence_summary=summary,
        reason_codes=codes,
        user_facing_rationale=rationale,
    )
