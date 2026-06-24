from __future__ import annotations

from typing import Any
from uuid import uuid4

from .schemas import ObserverDecision, PlanAdjustment


def build_observer_signals(
    *,
    completion_rate_7d: float | None,
    correctness_rate: float | None,
    mastery_delta: float | None,
    low_mastery_nodes: list[dict[str, Any]] | None = None,
    wrong_reason_tags: list[str] | None = None,
    recent_attempts: list[dict[str, Any]] | None = None,
    review_queue: list[dict[str, Any]] | None = None,
    phase_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    missing_data_strategy: dict[str, str] = {}
    completion = _rate_or_default(
        completion_rate_7d,
        default=0.8,
        missing_data_strategy=missing_data_strategy,
        key="completion_rate_7d",
        label="0.8",
    )
    correctness = _rate_or_default(
        correctness_rate,
        default=0.8,
        missing_data_strategy=missing_data_strategy,
        key="correctness_rate",
        label="0.8",
    )
    delta = _number_or_default(
        mastery_delta,
        default=0,
        missing_data_strategy=missing_data_strategy,
        key="mastery_delta",
        label="0",
    )
    signals: dict[str, Any] = {
        "completion_rate_7d": completion,
        "correctness_rate": correctness,
        "mastery_delta": delta,
        "low_mastery_nodes": low_mastery_nodes or [],
        "wrong_reason_tags": sorted(set(wrong_reason_tags or [])),
        "recent_attempts": recent_attempts or [],
        "missing_data_strategy": missing_data_strategy,
    }
    if review_queue is not None:
        signals["review_queue"] = review_queue
    if phase_assessment is not None:
        signals["phase_assessment"] = phase_assessment
    return signals


def decide_observer_action_from_signals(signals: dict[str, Any] | None) -> ObserverDecision:
    source = signals or {}
    normalized = build_observer_signals(
        completion_rate_7d=source.get("completion_rate_7d"),
        correctness_rate=source.get("correctness_rate"),
        mastery_delta=source.get("mastery_delta"),
        low_mastery_nodes=source.get("low_mastery_nodes", []),
        wrong_reason_tags=source.get("wrong_reason_tags", []),
        recent_attempts=source.get("recent_attempts", []),
        review_queue=source.get("review_queue"),
        phase_assessment=source.get("phase_assessment"),
    )
    normalized["missing_data_strategy"] = {
        **source.get("missing_data_strategy", {}),
        **normalized.get("missing_data_strategy", {}),
    }
    decision = decide_observer_action(
        completion_rate_7d=normalized["completion_rate_7d"],
        correctness_rate=normalized["correctness_rate"],
        mastery_delta=normalized["mastery_delta"],
    )
    return decision.model_copy(update={"evidence_json": normalized})


def decide_observer_action(
    *,
    completion_rate_7d: float,
    correctness_rate: float,
    mastery_delta: float,
) -> ObserverDecision:
    evidence = {
        "completion_rate_7d": completion_rate_7d,
        "correctness_rate": correctness_rate,
        "mastery_delta": mastery_delta,
    }
    if completion_rate_7d < 0.6:
        return ObserverDecision(
            decision="reduce",
            evidence_json=evidence,
            rationale="Seven-day completion rate is below the V1 load threshold.",
        )
    if correctness_rate < 0.6 or mastery_delta <= -10:
        return ObserverDecision(
            decision="remediate",
            evidence_json=evidence,
            rationale="Recent correctness or mastery trend indicates a gap.",
        )
    if completion_rate_7d >= 0.9 and correctness_rate >= 0.9 and mastery_delta >= 5:
        return ObserverDecision(
            decision="advance",
            evidence_json=evidence,
            rationale="Recent performance is strong enough to unlock harder work.",
        )
    return ObserverDecision(
        decision="keep",
        evidence_json=evidence,
        rationale="Learning signals remain within the current plan.",
    )


def generate_plan_adjustment(
    *,
    user_id: str,
    goal_id: str,
    previous_plan_id: str,
    decision: ObserverDecision,
    trigger_type: str = "observer",
    state_snapshot: dict[str, Any] | None = None,
    observer_signals: dict[str, Any] | None = None,
    manual_request: str = "",
) -> PlanAdjustment:
    signals = observer_signals or decision.evidence_json
    patch_by_decision = {
        "keep": {"no_change": True},
        "reduce": {"load_multiplier": 0.8, "defer_nonessential": True},
        "remediate": {"insert_review": True, "review_task_count": 2},
        "advance": {"unlock_next_nodes": True, "increase_difficulty": 1},
    }
    summary_by_decision = {
        "keep": {
            "decision": "keep",
            "changes": [],
            "summary": "Keep the current plan; structured learning signals are within range.",
        },
        "reduce": {
            "decision": "reduce",
            "changes": [{"type": "load", "label": "Reduce future daily load by 20%"}],
            "summary": "Reduce workload because recent completion signals are below threshold.",
        },
        "remediate": {
            "decision": "remediate",
            "changes": [{"type": "review", "label": "Add review tasks for weak knowledge nodes"}],
            "summary": "Add review because recent correctness or mastery trend indicates a gap.",
        },
        "advance": {
            "decision": "advance",
            "changes": [{"type": "progression", "label": "Unlock the next knowledge node"}],
            "summary": "Advance because recent performance is strong.",
        },
    }
    before_snapshot = _build_before_snapshot(state_snapshot, previous_plan_id)
    change_summary = summary_by_decision[decision.decision]
    after_snapshot = {
        "active_plan_id": previous_plan_id,
        "pending_patch": patch_by_decision[decision.decision],
        "change_summary": change_summary,
    }
    evidence_json = {
        "observer_signals": signals,
        "missing_data_strategy": signals.get("missing_data_strategy", {}),
    }
    if manual_request:
        evidence_json["manual_request"] = manual_request
    return PlanAdjustment(
        adjustment_id=f"adjustment-{uuid4()}",
        user_id=user_id,
        goal_id=goal_id,
        previous_plan_id=previous_plan_id,
        trigger_type=trigger_type,
        decision=decision.decision,
        status="proposed",
        evidence_json=evidence_json,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        plan_patch=patch_by_decision[decision.decision],
        change_summary=change_summary,
        rationale_json={
            "decision": decision.decision,
            "rationale": decision.rationale,
            "evidence_labels": _evidence_labels(signals),
            "missing_data_strategy": signals.get("missing_data_strategy", {}),
        },
    )


def _rate_or_default(
    value: float | None,
    *,
    default: float,
    missing_data_strategy: dict[str, str],
    key: str,
    label: str,
) -> float:
    if value is None:
        missing_data_strategy[key] = f"defaulted_to_{label}"
        return default
    return round(min(1.0, max(0.0, float(value))), 4)


def _number_or_default(
    value: float | None,
    *,
    default: float,
    missing_data_strategy: dict[str, str],
    key: str,
    label: str,
) -> float:
    if value is None:
        missing_data_strategy[key] = f"defaulted_to_{label}"
        return default
    return round(float(value), 4)


def _build_before_snapshot(state_snapshot: dict[str, Any] | None, previous_plan_id: str) -> dict[str, Any]:
    if not state_snapshot:
        return {"active_plan_id": previous_plan_id}
    current_state = state_snapshot.get("current_state", {})
    return {
        "active_plan": state_snapshot.get("active_plan", {"id": previous_plan_id}),
        "mastery_summary": state_snapshot.get("mastery_summary", {}),
        "review_queue": current_state.get("review_queue", []),
        "current_task": state_snapshot.get("current_task"),
    }


def _evidence_labels(signals: dict[str, Any]) -> list[str]:
    labels = [
        f"7-day completion rate: {signals.get('completion_rate_7d')}",
        f"recent correctness rate: {signals.get('correctness_rate')}",
        f"mastery delta: {signals.get('mastery_delta')}",
    ]
    if signals.get("low_mastery_nodes"):
        labels.append("low mastery nodes present")
    if signals.get("wrong_reason_tags"):
        labels.append("recent wrong-answer tags present")
    return labels
