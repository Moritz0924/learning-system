from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from backend.app.domain.memory import (
    MemoryCandidate,
    MemoryDecision,
    MemoryPrivacySettings,
    MemoryWriteReceipt,
)

from .helpers import FIXED_NOW, mastery_command, preference_command


def test_memory_write_contracts_are_frozen_and_forbid_unknown_fields() -> None:
    candidate = MemoryCandidate(
        candidate_id="candidate-1",
        origin="explicit_user_statement",
        command=preference_command(idempotency_key="memory-v1:explicit:request-1"),
        semantic_key="preference:explanation_style",
    )

    with pytest.raises(ValidationError):
        MemoryCandidate(
            candidate_id="candidate-2",
            origin="explicit_user_statement",
            command=preference_command(idempotency_key="memory-v1:explicit:request-2"),
            semantic_key="preference:explanation_style",
            raw_chat="remember this",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        MemoryPrivacySettings(enabled=True, provider_payload={})  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        candidate.semantic_key = "changed"  # type: ignore[misc]


def test_memory_candidate_rejects_policy_and_identifier_shape_errors() -> None:
    command = preference_command(idempotency_key="memory-v1:explicit:request-1")

    with pytest.raises(ValidationError):
        MemoryCandidate(
            candidate_id="",
            origin="explicit_user_statement",
            command=command,
            semantic_key="preference:key",
        )
    with pytest.raises(ValidationError):
        MemoryCandidate(
            candidate_id="candidate-1",
            origin="explicit_user_statement",
            command=command,
            semantic_key="preference:key",
            policy_version="memory-gate-v2",
        )


def test_memory_privacy_defaults_are_safe_and_backward_compatible() -> None:
    settings = MemoryPrivacySettings()

    assert settings.model_dump() == {
        "enabled": True,
        "allow_explicit_user": True,
        "allow_system_inference": False,
        "allow_learning_results": True,
    }


def test_memory_decision_and_receipt_are_strict_audit_contracts() -> None:
    candidate = MemoryCandidate(
        candidate_id="candidate-1",
        origin="learning_result",
        command=mastery_command(
            confidence=0.8,
            expires_at=FIXED_NOW + timedelta(days=30),
            idempotency_key="memory-v1:generated:abc",
        ),
        semantic_key="mastery:test-goal:python-basics",
    )
    decision = MemoryDecision(candidate=candidate, decision="approved", reason_code="policy_approved")
    receipt = MemoryWriteReceipt(
        candidate_id=candidate.candidate_id,
        origin=candidate.origin,
        status="saved",
        reason_code="created",
        memory_id="memory-1",
    )

    assert decision.candidate is candidate
    assert receipt.memory_id == "memory-1"
    with pytest.raises(ValidationError):
        MemoryWriteReceipt(
            candidate_id=candidate.candidate_id,
            origin=candidate.origin,
            status="rejected",
            reason_code="privacy_disabled",
            memory_id="memory-should-not-exist",
        )
