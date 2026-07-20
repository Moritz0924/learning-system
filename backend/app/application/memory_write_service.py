from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from backend.app.application.memory_privacy_service import MemoryPrivacyService
from backend.app.domain.memory import (
    CreateMemoryCommand,
    MemoryCandidate,
    MemoryDecision,
    MemoryIdempotencyConflict,
    MemoryPrivacySettings,
    MemoryRecord,
    MemoryWriteReceipt,
    evaluate_memory_candidates,
    validate_memory_command,
)
from backend.app.infrastructure.persistence.repositories.memory_repository import SQLAlchemyMemoryRepository


@dataclass
class MemoryWriteService:
    session: Session
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def save_decisions(
        self,
        *,
        user_id: str,
        goal_id: str,
        decisions: list[MemoryDecision],
    ) -> list[MemoryWriteReceipt]:
        now = _effective_now(self.clock())
        settings = MemoryPrivacyService(self.session).get(user_id=user_id, for_update=True)
        current_decisions = [
            self._recheck_decision(
                decision,
                settings=settings,
                user_id=user_id,
                goal_id=goal_id,
                now=now,
            )
            for decision in decisions
        ]
        repository = SQLAlchemyMemoryRepository(self.session)
        self._preflight_explicit_conflicts(repository, current_decisions, now=now)

        receipts: list[MemoryWriteReceipt] = []
        for decision in current_decisions:
            candidate = decision.candidate
            if decision.decision == "rejected":
                receipts.append(
                    MemoryWriteReceipt(
                        candidate_id=candidate.candidate_id,
                        origin=candidate.origin,
                        status="rejected",
                        reason_code=decision.reason_code,
                    )
                )
                continue

            existing = repository.get_by_idempotency_key(
                user_id=user_id,
                idempotency_key=candidate.command.idempotency_key,
            )
            try:
                record = repository.create_or_get(candidate.command, now=now)
            except MemoryIdempotencyConflict:
                if candidate.origin == "explicit_user_statement":
                    raise
                conflicted = repository.get_by_idempotency_key(
                    user_id=user_id,
                    idempotency_key=candidate.command.idempotency_key,
                )
                receipts.append(
                    MemoryWriteReceipt(
                        candidate_id=candidate.candidate_id,
                        origin=candidate.origin,
                        status="conflict",
                        reason_code="idempotency_conflict",
                        memory_id=None if conflicted is None else conflicted.id,
                    )
                )
                continue

            if record.is_enabled and (record.expires_at is None or record.expires_at > now):
                self._disable_superseded(repository, candidate=decision.candidate, current=record, now=now)
            receipts.append(
                MemoryWriteReceipt(
                    candidate_id=candidate.candidate_id,
                    origin=candidate.origin,
                    status="reused" if existing is not None else "saved",
                    reason_code="existing_match" if existing is not None else "created",
                    memory_id=record.id,
                )
            )
        return receipts

    def preflight_explicit_decisions(
        self,
        decisions: list[MemoryDecision],
    ) -> None:
        self._preflight_explicit_conflicts(
            SQLAlchemyMemoryRepository(self.session),
            decisions,
            now=_effective_now(self.clock()),
        )

    @staticmethod
    def _recheck_decision(
        decision: MemoryDecision,
        *,
        settings: MemoryPrivacySettings,
        user_id: str,
        goal_id: str,
        now: datetime,
    ) -> MemoryDecision:
        if decision.decision == "rejected":
            return decision
        return evaluate_memory_candidates(
            [decision.candidate],
            settings=settings,
            expected_user_id=user_id,
            expected_goal_id=goal_id,
            now=now,
        )[0]

    @staticmethod
    def _preflight_explicit_conflicts(
        repository: SQLAlchemyMemoryRepository,
        decisions: list[MemoryDecision],
        *,
        now: datetime,
    ) -> None:
        for decision in decisions:
            candidate = decision.candidate
            if decision.decision != "approved" or candidate.origin != "explicit_user_statement":
                continue
            existing = repository.get_by_idempotency_key(
                user_id=candidate.command.user_id,
                idempotency_key=candidate.command.idempotency_key,
            )
            if existing is not None and not _record_matches_command(existing, candidate.command, now=now):
                raise MemoryIdempotencyConflict("Memory idempotency conflict.")

    @staticmethod
    def _disable_superseded(
        repository: SQLAlchemyMemoryRepository,
        *,
        candidate: MemoryCandidate,
        current: MemoryRecord,
        now: datetime,
    ) -> None:
        command = candidate.command
        if command.memory_type == "learning_milestone":
            return
        records = repository.list_active(
            user_id=command.user_id,
            goal_id=command.goal_id,
            memory_types={command.memory_type},
            include_user_scope=False,
            limit=100,
            now=now,
        )
        for record in records:
            if record.id == current.id or not _same_semantic_slot(record, current):
                continue
            repository.disable(
                user_id=command.user_id,
                memory_id=record.id,
                reason="superseded",
                now=now,
            )


def _record_matches_command(
    record: MemoryRecord,
    command: CreateMemoryCommand,
    *,
    now: datetime,
) -> bool:
    validated = validate_memory_command(command, now=now)
    normalized = validated.command
    return (
        record.user_id == normalized.user_id
        and record.goal_id == normalized.goal_id
        and record.memory_type == normalized.memory_type
        and record.schema_version == normalized.schema_version
        and record.content_hash == validated.content_hash
        and record.source_kind == normalized.source_kind
        and record.source_ref_id == normalized.source_ref_id
        and record.source_metadata == normalized.source_metadata
        and record.importance == normalized.importance
        and record.confidence == normalized.confidence
        and record.expires_at == normalized.expires_at
        and record.idempotency_key == normalized.idempotency_key
    )


def _same_semantic_slot(left: MemoryRecord, right: MemoryRecord) -> bool:
    if left.memory_type != right.memory_type or left.goal_id != right.goal_id:
        return False
    if left.memory_type == "learning_preference":
        return left.content.get("preference_key") == right.content.get("preference_key")
    if left.memory_type == "long_term_goal":
        return True
    if left.memory_type == "mastery_summary":
        return left.content.get("knowledge_node_id") == right.content.get("knowledge_node_id")
    return False


def _effective_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)
