from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import UUID

from adaptive_tutor.phase2.schemas import AssessmentAttemptResult, MasteryUpdate

from backend.app.domain.memory import (
    MEMORY_GATE_POLICY_VERSION,
    CreateMemoryCommand,
    MemoryCandidate,
    MemoryCandidateOrigin,
    MemoryType,
)


def generated_memory_idempotency_key(
    *,
    source_ref_id: str,
    memory_type: MemoryType,
    semantic_key: str,
    policy_version: str = MEMORY_GATE_POLICY_VERSION,
) -> str:
    canonical = json.dumps(
        {
            "memory_type": memory_type,
            "policy_version": policy_version,
            "semantic_key": semantic_key,
            "source_ref_id": source_ref_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"memory-v1:generated:{sha256(canonical.encode('utf-8')).hexdigest()}"


def build_explicit_preference_candidate(
    *,
    user_id: str,
    request_id: str | UUID,
    preference_key: str,
    preference_value: str | bool | float | list[str],
) -> MemoryCandidate:
    normalized_request_id = _request_id(request_id)
    command = CreateMemoryCommand(
        user_id=user_id,
        goal_id=None,
        memory_type="learning_preference",
        content={
            "preference_key": preference_key,
            "preference_value": preference_value,
        },
        source_kind="explicit_user",
        importance=0.5,
        confidence=1.0,
        idempotency_key=f"memory-v1:explicit:{normalized_request_id}",
    )
    return _candidate(
        origin="explicit_user_statement",
        command=command,
        semantic_key=f"preference:{preference_key.strip()}",
    )


def build_explicit_goal_candidate(
    *,
    user_id: str,
    goal_id: str,
    request_id: str | UUID,
    title: str,
    target_outcome: str,
    deadline: date | str | None = None,
) -> MemoryCandidate:
    normalized_request_id = _request_id(request_id)
    normalized_deadline = deadline.isoformat() if isinstance(deadline, date) else deadline
    command = CreateMemoryCommand(
        user_id=user_id,
        goal_id=goal_id,
        memory_type="long_term_goal",
        content={
            "title": title,
            "target_outcome": target_outcome,
            "deadline": normalized_deadline,
        },
        source_kind="explicit_user",
        importance=0.7,
        confidence=1.0,
        idempotency_key=f"memory-v1:explicit:{normalized_request_id}",
    )
    return _candidate(
        origin="explicit_user_statement",
        command=command,
        semantic_key=f"long_term_goal:{goal_id}",
    )


def build_learning_result_candidates(
    *,
    user_id: str,
    goal_id: str,
    assessment_result: AssessmentAttemptResult,
    mastery_updates: list[MasteryUpdate],
    now: datetime,
) -> list[MemoryCandidate]:
    effective_now = _utc_now(now)
    candidates: list[MemoryCandidate] = []
    for update in mastery_updates:
        semantic_key = f"mastery:{goal_id}:{update.knowledge_node_id}"
        mastery_command = CreateMemoryCommand(
            user_id=user_id,
            goal_id=goal_id,
            memory_type="mastery_summary",
            content={
                "knowledge_node_id": update.knowledge_node_id,
                "score": update.new_score,
                "confidence": update.confidence,
                "evidence_count": max(1, update.evidence_count),
                "calculation_version": update.calculation_version,
            },
            source_kind="assessment",
            source_ref_id=assessment_result.attempt_id,
            source_metadata={"assessment_id": assessment_result.assessment_id},
            importance=0.8,
            confidence=update.confidence,
            expires_at=effective_now + timedelta(days=30),
            idempotency_key=generated_memory_idempotency_key(
                source_ref_id=assessment_result.attempt_id,
                memory_type="mastery_summary",
                semantic_key=semantic_key,
            ),
        )
        candidates.append(
            _candidate(
                origin="learning_result",
                command=mastery_command,
                semantic_key=semantic_key,
            )
        )

        if update.previous_score < 80 <= update.new_score and update.confidence >= 0.7:
            milestone_code = f"mastery-80:{update.knowledge_node_id}"
            milestone_semantic_key = f"milestone:{goal_id}:{milestone_code}"
            milestone_command = CreateMemoryCommand(
                user_id=user_id,
                goal_id=goal_id,
                memory_type="learning_milestone",
                content={
                    "milestone_code": milestone_code,
                    "title": f"Reached 80 mastery in {update.knowledge_node_id}",
                    "achieved_at": effective_now,
                    "evidence_refs": [assessment_result.attempt_id],
                },
                source_kind="assessment",
                source_ref_id=assessment_result.attempt_id,
                source_metadata={"assessment_id": assessment_result.assessment_id},
                importance=0.8,
                confidence=update.confidence,
                expires_at=None,
                idempotency_key=generated_memory_idempotency_key(
                    source_ref_id=assessment_result.attempt_id,
                    memory_type="learning_milestone",
                    semantic_key=milestone_semantic_key,
                ),
            )
            candidates.append(
                _candidate(
                    origin="learning_result",
                    command=milestone_command,
                    semantic_key=milestone_semantic_key,
                )
            )
    return candidates


def _candidate(
    *,
    origin: MemoryCandidateOrigin,
    command: CreateMemoryCommand,
    semantic_key: str,
) -> MemoryCandidate:
    candidate_digest = sha256(
        f"{command.idempotency_key}:{semantic_key}:{MEMORY_GATE_POLICY_VERSION}".encode("utf-8")
    ).hexdigest()
    return MemoryCandidate(
        candidate_id=f"memory-candidate-{candidate_digest[:32]}",
        origin=origin,
        command=command,
        semantic_key=semantic_key,
        policy_version=MEMORY_GATE_POLICY_VERSION,
    )


def _request_id(value: str | UUID) -> str:
    return str(value if isinstance(value, UUID) else UUID(str(value)))


def _utc_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)
