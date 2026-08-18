from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import exp
from typing import Any

from .contracts import MasteryEvidenceV2, MasteryUpdateV2


QUESTION_TYPE_WEIGHT = {"choice": 0.65, "explain": 0.90, "code_reading": 1.00, "scenario": 1.10}
GRADING_MODE_WEIGHT = {
    "deterministic_exact": 1.00,
    "remote_structured": 1.00,
    "deterministic_fallback": 0.40,
    "manual_review_required": 0.00,
}


def calculate_mastery_updates(
    previous: dict[str, dict[str, Any]],
    evidence: list[MasteryEvidenceV2],
    *,
    auto_min_confidence: float = 0.65,
    now: datetime | None = None,
) -> list[MasteryUpdateV2]:
    now = now or datetime.now(timezone.utc)
    grouped: dict[str, list[MasteryEvidenceV2]] = defaultdict(list)
    for item in evidence:
        grouped[item.knowledge_node_id].append(item)
    updates: list[MasteryUpdateV2] = []
    for node_id in sorted(set(previous) | set(grouped)):
        state = previous.get(node_id, {})
        previous_score = _bounded(state.get("score", 60), 0, 100)
        previous_confidence = _bounded(state.get("confidence", 0.1), 0, 1)
        prior_weight = max(0.0, float(state.get("evidence_weight", 0)))
        accepted: list[tuple[MasteryEvidenceV2, float]] = []
        rejected = 0
        for item in grouped[node_id]:
            weight = QUESTION_TYPE_WEIGHT[item.question_type] * GRADING_MODE_WEIGHT[item.grading_mode] * item.grader_confidence
            if item.eligible_for_mastery and weight > 0:
                accepted.append((item, weight))
            else:
                rejected += 1
        total_weight = sum(weight for _, weight in accepted)
        if not accepted:
            updates.append(
                MasteryUpdateV2(
                    knowledge_node_id=node_id,
                    previous_score=previous_score,
                    evidence_score=None,
                    new_score=previous_score,
                    previous_confidence=previous_confidence,
                    new_confidence=previous_confidence,
                    accepted_evidence_count=0,
                    rejected_evidence_count=rejected,
                    total_evidence_weight=0,
                    automatic_adjustment_eligible=False,
                    source_breakdown={"previous_evidence_weight": prior_weight},
                    reason_codes=["no_reliable_evidence"],
                )
            )
            continue
        evidence_score = sum(item.score * weight for item, weight in accepted) / total_weight
        alpha = min(0.45, total_weight / (4.0 + total_weight))
        decay = _decay(state.get("last_evidence_at"), now)
        new_score = _bounded(previous_score + alpha * (evidence_score - previous_score) - decay, 0, 100)
        new_confidence = _bounded(1 - exp(-(prior_weight + total_weight) / 6), 0.1, 0.98)
        requires_review = any(item.grading_mode == "manual_review_required" for item in grouped[node_id])
        eligible = total_weight >= 1.0 and new_confidence >= auto_min_confidence and not requires_review
        updates.append(
            MasteryUpdateV2(
                knowledge_node_id=node_id,
                previous_score=round(previous_score, 2),
                evidence_score=round(evidence_score, 2),
                new_score=round(new_score, 2),
                previous_confidence=round(previous_confidence, 4),
                new_confidence=round(new_confidence, 4),
                accepted_evidence_count=len(accepted),
                rejected_evidence_count=rejected,
                total_evidence_weight=round(total_weight, 4),
                automatic_adjustment_eligible=eligible,
                source_breakdown={
                    "previous_evidence_weight": prior_weight,
                    "new_evidence_weight": round(total_weight, 4),
                    "forgetting_decay": round(decay, 2),
                },
                reason_codes=["reliable_evidence_accepted"] if eligible else ["insufficient_reliable_evidence"],
            )
        )
    return updates


def _decay(last_evidence_at: Any, now: datetime) -> float:
    if not isinstance(last_evidence_at, datetime):
        return 0.0
    if last_evidence_at.tzinfo is None:
        last_evidence_at = last_evidence_at.replace(tzinfo=timezone.utc)
    days = max(0, (now - last_evidence_at).days)
    return min(5.0, max(0, days - 14) * 0.15)


def _bounded(value: Any, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))
