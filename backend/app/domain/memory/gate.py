from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from .errors import MemoryError, MemoryGateInvariantError, MemoryGateLimitError
from .validation import validate_memory_command
from .write_contracts import MemoryCandidate, MemoryDecision, MemoryPrivacySettings


MEMORY_GATE_POLICY_VERSION = "memory-gate-v1"
MEMORY_GATE_CANDIDATE_LIMIT = 32

_ORIGIN_SOURCE_KINDS = {
    "explicit_user_statement": frozenset({"explicit_user"}),
    "system_inference": frozenset({"system_derived"}),
    "learning_result": frozenset({"assessment", "mastery_record", "learning_event"}),
}


def evaluate_memory_candidates(
    candidates: Sequence[MemoryCandidate],
    *,
    settings: MemoryPrivacySettings,
    expected_user_id: str,
    expected_goal_id: str,
    now: datetime,
) -> list[MemoryDecision]:
    if len(candidates) > MEMORY_GATE_CANDIDATE_LIMIT:
        raise MemoryGateLimitError("Memory candidate limit exceeded.")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    effective_now = now.astimezone(timezone.utc)

    decisions: list[MemoryDecision] = []
    seen_idempotency_keys: set[str] = set()
    for candidate in candidates:
        _validate_candidate_invariants(
            candidate,
            expected_user_id=expected_user_id,
            expected_goal_id=expected_goal_id,
            now=effective_now,
        )
        key = candidate.command.idempotency_key
        if key in seen_idempotency_keys:
            decisions.append(_reject(candidate, "duplicate_idempotency_key"))
            continue
        seen_idempotency_keys.add(key)

        privacy_reason = _privacy_rejection_reason(candidate, settings)
        if privacy_reason is not None:
            decisions.append(_reject(candidate, privacy_reason))
            continue
        if not _meets_confidence_threshold(candidate):
            decisions.append(_reject(candidate, "confidence_below_threshold"))
            continue
        decisions.append(
            MemoryDecision(
                candidate=candidate,
                decision="approved",
                reason_code="policy_approved",
            )
        )
    return decisions


def _validate_candidate_invariants(
    candidate: MemoryCandidate,
    *,
    expected_user_id: str,
    expected_goal_id: str,
    now: datetime,
) -> None:
    command = candidate.command
    if command.user_id != expected_user_id:
        raise MemoryGateInvariantError("Memory candidate user ownership mismatch.")
    if command.goal_id is not None and command.goal_id != expected_goal_id:
        raise MemoryGateInvariantError("Memory candidate goal ownership mismatch.")
    if command.source_kind not in _ORIGIN_SOURCE_KINDS[candidate.origin]:
        raise MemoryGateInvariantError("Memory candidate source mapping mismatch.")
    try:
        validate_memory_command(command, now=now)
    except MemoryError as error:
        raise MemoryGateInvariantError("Memory candidate command is invalid.") from error


def _privacy_rejection_reason(
    candidate: MemoryCandidate,
    settings: MemoryPrivacySettings,
) -> str | None:
    if not settings.enabled:
        return "memory_privacy_disabled"
    source_allowed = {
        "explicit_user_statement": settings.allow_explicit_user,
        "system_inference": settings.allow_system_inference,
        "learning_result": settings.allow_learning_results,
    }[candidate.origin]
    return None if source_allowed else "source_privacy_disabled"


def _meets_confidence_threshold(candidate: MemoryCandidate) -> bool:
    command = candidate.command
    if candidate.origin == "system_inference":
        return command.confidence >= 0.85
    if command.memory_type == "mastery_summary":
        content_confidence = command.content.get("confidence")
        return (
            command.confidence >= 0.6
            and isinstance(content_confidence, (int, float))
            and not isinstance(content_confidence, bool)
            and float(content_confidence) >= 0.6
        )
    if command.memory_type == "learning_milestone":
        return command.confidence >= 0.7
    return True


def _reject(candidate: MemoryCandidate, reason_code: str) -> MemoryDecision:
    return MemoryDecision(candidate=candidate, decision="rejected", reason_code=reason_code)
