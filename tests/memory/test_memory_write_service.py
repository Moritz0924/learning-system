from __future__ import annotations

from datetime import timedelta

import pytest

from backend.app.application.memory_privacy_service import LONG_TERM_MEMORY_PRIVACY_KEY
from backend.app.application.memory_write_service import MemoryWriteService
from backend.app.domain.memory import (
    MemoryCandidate,
    MemoryDecision,
    MemoryIdempotencyConflict,
    MemoryPrivacySettings,
    evaluate_memory_candidates,
)
from backend.app.infrastructure.persistence.repositories.memory_repository import SQLAlchemyMemoryRepository
from backend.app.models import LearnerProfile

from .helpers import FIXED_NOW, add_memory_scope, mastery_command, milestone_command, preference_command


def _profile(db_session, user_id: str, settings: MemoryPrivacySettings | None = None) -> LearnerProfile:
    profile = LearnerProfile(
        user_id=user_id,
        privacy_settings={LONG_TERM_MEMORY_PRIVACY_KEY: (settings or MemoryPrivacySettings()).model_dump()},
    )
    db_session.add(profile)
    db_session.flush()
    return profile


def _candidate(command, *, candidate_id: str, origin: str, semantic_key: str) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        origin=origin,
        command=command,
        semantic_key=semantic_key,
    )


def _approved(candidate: MemoryCandidate) -> MemoryDecision:
    return MemoryDecision(candidate=candidate, decision="approved", reason_code="policy_approved")


def test_write_service_saves_then_reuses_same_explicit_request_without_committing(db_session) -> None:
    user_id, goal_id = add_memory_scope(db_session)
    _profile(db_session, user_id)
    candidate = _candidate(
        preference_command(user_id=user_id, idempotency_key="memory-v1:explicit:request-1"),
        candidate_id="candidate-1",
        origin="explicit_user_statement",
        semantic_key="preference:explanation_style",
    )
    service = MemoryWriteService(db_session, clock=lambda: FIXED_NOW)

    [created] = service.save_decisions(
        user_id=user_id,
        goal_id=goal_id,
        decisions=[_approved(candidate)],
    )
    [reused] = service.save_decisions(
        user_id=user_id,
        goal_id=goal_id,
        decisions=[_approved(candidate)],
    )

    assert created.status == "saved"
    assert reused.status == "reused"
    assert reused.memory_id == created.memory_id
    assert db_session.in_transaction()


def test_write_service_rechecks_privacy_and_skips_when_disabled_after_gate(db_session) -> None:
    user_id, goal_id = add_memory_scope(db_session)
    profile = _profile(db_session, user_id)
    candidate = _candidate(
        preference_command(user_id=user_id, idempotency_key="memory-v1:explicit:request-privacy"),
        candidate_id="candidate-privacy",
        origin="explicit_user_statement",
        semantic_key="preference:explanation_style",
    )
    [decision] = evaluate_memory_candidates(
        [candidate],
        settings=MemoryPrivacySettings(),
        expected_user_id=user_id,
        expected_goal_id=goal_id,
        now=FIXED_NOW,
    )
    profile.privacy_settings = {
        LONG_TERM_MEMORY_PRIVACY_KEY: MemoryPrivacySettings(enabled=False).model_dump()
    }
    db_session.flush()

    [receipt] = MemoryWriteService(db_session, clock=lambda: FIXED_NOW).save_decisions(
        user_id=user_id,
        goal_id=goal_id,
        decisions=[decision],
    )

    assert (receipt.status, receipt.reason_code, receipt.memory_id) == (
        "rejected",
        "memory_privacy_disabled",
        None,
    )
    assert SQLAlchemyMemoryRepository(db_session).get_by_idempotency_key(
        user_id=user_id,
        idempotency_key=candidate.command.idempotency_key,
    ) is None


def test_new_semantic_slot_soft_disables_previous_record(db_session) -> None:
    user_id, goal_id = add_memory_scope(db_session)
    _profile(db_session, user_id)
    service = MemoryWriteService(db_session, clock=lambda: FIXED_NOW)
    first = _candidate(
        preference_command(
            user_id=user_id,
            content={"preference_key": "style", "preference_value": "concise"},
            idempotency_key="memory-v1:explicit:first",
        ),
        candidate_id="candidate-first",
        origin="explicit_user_statement",
        semantic_key="preference:style",
    )
    second = _candidate(
        preference_command(
            user_id=user_id,
            content={"preference_key": "style", "preference_value": "examples"},
            idempotency_key="memory-v1:explicit:second",
        ),
        candidate_id="candidate-second",
        origin="explicit_user_statement",
        semantic_key="preference:style",
    )

    [first_receipt] = service.save_decisions(user_id=user_id, goal_id=goal_id, decisions=[_approved(first)])
    [second_receipt] = service.save_decisions(user_id=user_id, goal_id=goal_id, decisions=[_approved(second)])

    repository = SQLAlchemyMemoryRepository(db_session)
    previous = repository.get_by_id(user_id=user_id, memory_id=first_receipt.memory_id, include_inactive=True)
    current = repository.get_by_id(user_id=user_id, memory_id=second_receipt.memory_id, include_inactive=True)
    assert previous is not None and not previous.is_enabled
    assert previous.disabled_reason == "superseded"
    assert current is not None and current.is_enabled


def test_explicit_idempotency_conflict_raises_before_any_batch_write(db_session) -> None:
    user_id, goal_id = add_memory_scope(db_session)
    _profile(db_session, user_id)
    repository = SQLAlchemyMemoryRepository(db_session)
    repository.create_or_get(
        preference_command(
            user_id=user_id,
            content={"preference_key": "style", "preference_value": "first"},
            idempotency_key="memory-v1:explicit:conflict",
        ),
        now=FIXED_NOW,
    )
    conflicting = _candidate(
        preference_command(
            user_id=user_id,
            content={"preference_key": "style", "preference_value": "changed"},
            idempotency_key="memory-v1:explicit:conflict",
        ),
        candidate_id="candidate-conflict",
        origin="explicit_user_statement",
        semantic_key="preference:style",
    )
    would_write = _candidate(
        preference_command(user_id=user_id, idempotency_key="memory-v1:explicit:new"),
        candidate_id="candidate-new",
        origin="explicit_user_statement",
        semantic_key="preference:explanation_style",
    )

    with pytest.raises(MemoryIdempotencyConflict):
        MemoryWriteService(db_session, clock=lambda: FIXED_NOW).save_decisions(
            user_id=user_id,
            goal_id=goal_id,
            decisions=[_approved(would_write), _approved(conflicting)],
        )

    assert repository.get_by_idempotency_key(
        user_id=user_id,
        idempotency_key="memory-v1:explicit:new",
    ) is None


def test_learning_result_conflict_is_audited_and_does_not_abort_other_writes(db_session) -> None:
    user_id, goal_id = add_memory_scope(db_session)
    _profile(db_session, user_id)
    repository = SQLAlchemyMemoryRepository(db_session)
    original = mastery_command(
        user_id=user_id,
        goal_id=goal_id,
        source_kind="assessment",
        source_ref_id="attempt-1",
        idempotency_key="memory-v1:generated:conflict",
        expires_at=FIXED_NOW + timedelta(days=30),
    )
    repository.create_or_get(original, now=FIXED_NOW)
    conflict = _candidate(
        original.model_copy(update={"importance": 0.9}),
        candidate_id="learning-conflict",
        origin="learning_result",
        semantic_key="mastery:test-goal:python-basics",
    )
    other = _candidate(
        mastery_command(
            user_id=user_id,
            goal_id=goal_id,
            source_kind="assessment",
            source_ref_id="attempt-2",
            idempotency_key="memory-v1:generated:other",
            expires_at=FIXED_NOW + timedelta(days=30),
            content={
                "knowledge_node_id": "python-advanced",
                "score": 81,
                "confidence": 0.8,
                "evidence_count": 2,
                "calculation_version": "mastery-v1",
            },
        ),
        candidate_id="learning-other",
        origin="learning_result",
        semantic_key="mastery:test-goal:python-advanced",
    )

    receipts = MemoryWriteService(db_session, clock=lambda: FIXED_NOW).save_decisions(
        user_id=user_id,
        goal_id=goal_id,
        decisions=[_approved(conflict), _approved(other)],
    )

    assert [(receipt.status, receipt.reason_code) for receipt in receipts] == [
        ("conflict", "idempotency_conflict"),
        ("saved", "created"),
    ]


def test_mastery_semantic_slot_is_replaced_but_milestones_always_append(db_session) -> None:
    user_id, goal_id = add_memory_scope(db_session)
    _profile(db_session, user_id)
    service = MemoryWriteService(db_session, clock=lambda: FIXED_NOW)
    mastery_first = _candidate(
        mastery_command(
            user_id=user_id,
            goal_id=goal_id,
            source_kind="assessment",
            source_ref_id="attempt-1",
            idempotency_key="memory-v1:generated:mastery-first",
            expires_at=FIXED_NOW + timedelta(days=30),
        ),
        candidate_id="mastery-first",
        origin="learning_result",
        semantic_key=f"mastery:{goal_id}:python-basics",
    )
    mastery_second = _candidate(
        mastery_command(
            user_id=user_id,
            goal_id=goal_id,
            source_kind="assessment",
            source_ref_id="attempt-2",
            content={
                "knowledge_node_id": "python-basics",
                "score": 92,
                "confidence": 0.9,
                "evidence_count": 4,
                "calculation_version": "mastery-v1",
            },
            confidence=0.9,
            idempotency_key="memory-v1:generated:mastery-second",
            expires_at=FIXED_NOW + timedelta(days=30),
        ),
        candidate_id="mastery-second",
        origin="learning_result",
        semantic_key=f"mastery:{goal_id}:python-basics",
    )
    milestone_first = _candidate(
        milestone_command(
            user_id=user_id,
            goal_id=goal_id,
            source_ref_id="attempt-1",
            idempotency_key="memory-v1:generated:milestone-first",
        ),
        candidate_id="milestone-first",
        origin="learning_result",
        semantic_key=f"milestone:{goal_id}:python-basics",
    )
    milestone_second = _candidate(
        milestone_command(
            user_id=user_id,
            goal_id=goal_id,
            source_ref_id="attempt-2",
            idempotency_key="memory-v1:generated:milestone-second",
        ),
        candidate_id="milestone-second",
        origin="learning_result",
        semantic_key=f"milestone:{goal_id}:python-basics",
    )

    first_mastery_receipt = service.save_decisions(
        user_id=user_id,
        goal_id=goal_id,
        decisions=[_approved(mastery_first)],
    )[0]
    second_mastery_receipt = service.save_decisions(
        user_id=user_id,
        goal_id=goal_id,
        decisions=[_approved(mastery_second)],
    )[0]
    milestone_receipts = service.save_decisions(
        user_id=user_id,
        goal_id=goal_id,
        decisions=[_approved(milestone_first), _approved(milestone_second)],
    )

    repository = SQLAlchemyMemoryRepository(db_session)
    first_mastery_record = repository.get_by_id(
        user_id=user_id,
        memory_id=first_mastery_receipt.memory_id,
        include_inactive=True,
    )
    second_mastery_record = repository.get_by_id(
        user_id=user_id,
        memory_id=second_mastery_receipt.memory_id,
        include_inactive=True,
    )
    assert first_mastery_record is not None and first_mastery_record.disabled_reason == "superseded"
    assert second_mastery_record is not None and second_mastery_record.is_enabled
    assert all(
        repository.get_by_id(
            user_id=user_id,
            memory_id=receipt.memory_id,
            include_inactive=True,
        ).is_enabled
        for receipt in milestone_receipts
    )
