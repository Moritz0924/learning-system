from __future__ import annotations

from datetime import timedelta

import pytest

from backend.app.domain.memory import (
    MemoryCandidate,
    MemoryGateInvariantError,
    MemoryGateLimitError,
    MemoryPrivacySettings,
    evaluate_memory_candidates,
)

from .helpers import FIXED_NOW, mastery_command, milestone_command, preference_command


def _candidate(*, candidate_id: str = "candidate-1", origin: str = "explicit_user_statement", command=None):
    return MemoryCandidate(
        candidate_id=candidate_id,
        origin=origin,
        command=command
        or preference_command(idempotency_key=f"memory-v1:explicit:{candidate_id}"),
        semantic_key=f"semantic:{candidate_id}",
    )


def _evaluate(candidates, settings: MemoryPrivacySettings | None = None):
    return evaluate_memory_candidates(
        candidates,
        settings=settings or MemoryPrivacySettings(),
        expected_user_id="test-user",
        expected_goal_id="test-goal",
        now=FIXED_NOW,
    )


def test_gate_approves_explicit_and_learning_results_but_rejects_system_inference_by_default() -> None:
    candidates = [
        _candidate(candidate_id="explicit"),
        _candidate(
            candidate_id="learning",
            origin="learning_result",
            command=mastery_command(
                confidence=0.8,
                expires_at=FIXED_NOW + timedelta(days=30),
                idempotency_key="memory-v1:generated:learning",
            ),
        ),
        _candidate(
            candidate_id="system",
            origin="system_inference",
            command=preference_command(
                source_kind="system_derived",
                source_ref_id="evidence-1",
                confidence=0.9,
                idempotency_key="memory-v1:generated:system",
            ),
        ),
    ]

    decisions = _evaluate(candidates)

    assert [(item.decision, item.reason_code) for item in decisions] == [
        ("approved", "policy_approved"),
        ("approved", "policy_approved"),
        ("rejected", "source_privacy_disabled"),
    ]


@pytest.mark.parametrize(
    ("settings", "reason"),
    [
        (MemoryPrivacySettings(enabled=False), "memory_privacy_disabled"),
        (MemoryPrivacySettings(allow_explicit_user=False), "source_privacy_disabled"),
    ],
)
def test_gate_records_privacy_rejections(settings: MemoryPrivacySettings, reason: str) -> None:
    [decision] = _evaluate([_candidate()], settings)

    assert decision.decision == "rejected"
    assert decision.reason_code == reason


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (
            _candidate(
                candidate_id="mastery-low",
                origin="learning_result",
                command=mastery_command(
                    confidence=0.59,
                    content={
                        "knowledge_node_id": "python-basics",
                        "score": 79,
                        "confidence": 0.59,
                        "evidence_count": 2,
                        "calculation_version": "mastery-v1",
                    },
                    expires_at=FIXED_NOW + timedelta(days=30),
                    idempotency_key="memory-v1:generated:mastery-low",
                ),
            ),
            "confidence_below_threshold",
        ),
        (
            _candidate(
                candidate_id="milestone-low",
                origin="learning_result",
                command=milestone_command(
                    confidence=0.69,
                    idempotency_key="memory-v1:generated:milestone-low",
                ),
            ),
            "confidence_below_threshold",
        ),
        (
            _candidate(
                candidate_id="system-low",
                origin="system_inference",
                command=preference_command(
                    source_kind="system_derived",
                    source_ref_id="evidence-1",
                    confidence=0.84,
                    idempotency_key="memory-v1:generated:system-low",
                ),
            ),
            "confidence_below_threshold",
        ),
    ],
)
def test_gate_enforces_origin_and_type_confidence_thresholds(candidate: MemoryCandidate, reason: str) -> None:
    settings = MemoryPrivacySettings(allow_system_inference=True)

    [decision] = _evaluate([candidate], settings)

    assert (decision.decision, decision.reason_code) == ("rejected", reason)


def test_gate_only_approves_first_idempotency_key_in_a_batch() -> None:
    command = preference_command(idempotency_key="memory-v1:explicit:same")

    decisions = _evaluate(
        [
            _candidate(candidate_id="first", command=command),
            _candidate(candidate_id="second", command=command),
        ]
    )

    assert [(item.decision, item.reason_code) for item in decisions] == [
        ("approved", "policy_approved"),
        ("rejected", "duplicate_idempotency_key"),
    ]


def test_gate_rejects_more_than_32_candidates_before_processing() -> None:
    candidates = [_candidate(candidate_id=f"candidate-{index}") for index in range(33)]

    with pytest.raises(MemoryGateLimitError):
        _evaluate(candidates)


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate(
            candidate_id="wrong-user",
            command=preference_command(
                user_id="other-user",
                idempotency_key="memory-v1:explicit:wrong-user",
            ),
        ),
        _candidate(
            candidate_id="wrong-goal",
            origin="learning_result",
            command=mastery_command(
                goal_id="other-goal",
                expires_at=FIXED_NOW + timedelta(days=30),
                idempotency_key="memory-v1:generated:wrong-goal",
            ),
        ),
        _candidate(
            candidate_id="wrong-source",
            origin="explicit_user_statement",
            command=preference_command(
                source_kind="system_derived",
                source_ref_id="evidence-1",
                idempotency_key="memory-v1:explicit:wrong-source",
            ),
        ),
    ],
)
def test_gate_fails_closed_on_identity_scope_or_source_mapping_mismatch(candidate: MemoryCandidate) -> None:
    with pytest.raises(MemoryGateInvariantError):
        _evaluate([candidate])
