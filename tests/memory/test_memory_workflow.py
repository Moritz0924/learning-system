from __future__ import annotations

from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.schemas import TutorRunRequest
from backend.app.application.memory_candidate_service import build_explicit_preference_candidate
from backend.app.domain.memory import MemoryDecision, MemoryPrivacySettings


def test_memory_gate_receives_only_narrow_structured_inputs_and_emits_save_action() -> None:
    dependencies = build_mock_phase2_dependencies()
    candidate = build_explicit_preference_candidate(
        user_id="user-1",
        request_id="00000000-0000-4000-8000-000000000001",
        preference_key="style",
        preference_value="examples",
    )
    captured: dict = {}

    def gate(**kwargs):
        captured.update(kwargs)
        return [
            MemoryDecision(
                candidate=candidate,
                decision="approved",
                reason_code="policy_approved",
            )
        ]

    dependencies.memory_gate = gate

    result = Phase2TutorEngine(dependencies).run(
        TutorRunRequest(
            trigger_type="chat",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            user_message="This raw message must not reach the memory gate.",
            memory_candidates=[candidate],
        )
    )

    assert set(captured) == {
        "user_id",
        "goal_id",
        "explicit_candidates",
        "assessment_result",
        "mastery_updates",
        "privacy_settings",
    }
    assert captured["explicit_candidates"] == [candidate]
    assert captured["privacy_settings"] == MemoryPrivacySettings()
    assert all("raw message" not in repr(value) for key, value in captured.items() if key != "explicit_candidates")
    save_action = next(action for action in result.workflow_actions if action.action_type == "save_memory")
    assert save_action.user_id == "user-1"
    assert save_action.goal_id == "goal-1"
    assert save_action.memory_decisions[0].candidate.candidate_id == candidate.candidate_id


def test_planner_routes_through_memory_gate_before_persist() -> None:
    dependencies = build_mock_phase2_dependencies()
    calls: list[dict] = []
    dependencies.memory_gate = lambda **kwargs: calls.append(kwargs) or []

    result = Phase2TutorEngine(dependencies).run(
        TutorRunRequest(
            trigger_type="manual_replan",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            user_message="Rebalance my plan.",
        )
    )

    assert len(calls) == 1
    assert [entry["node"] for entry in result.audit_log][-3:] == ["planner", "memory_gate", "persist"]


def test_assessment_mastery_updates_generate_learning_result_memory_actions() -> None:
    dependencies = build_mock_phase2_dependencies()
    draft = dependencies.assessment_repository.save_assessment_draft(
        dependencies.assessment_factory("daily", ["rag_foundations"])
    )

    result = Phase2TutorEngine(dependencies).run(
        TutorRunRequest(
            trigger_type="assessment_submitted",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            assessment_id=draft.assessment_id,
            submitted_answers={item.item_id: item.reference_answer for item in draft.items},
        )
    )

    save_action = next(action for action in result.workflow_actions if action.action_type == "save_memory")
    approved = [
        decision
        for decision in save_action.memory_decisions
        if decision.decision == "approved"
    ]
    assert approved
    assert all(decision.candidate.origin == "learning_result" for decision in approved)
    assert any(
        decision.candidate.command.memory_type == "mastery_summary"
        for decision in approved
    )


def test_memory_gate_audit_payload_excludes_content_keys_and_hashes() -> None:
    dependencies = build_mock_phase2_dependencies()
    candidate = build_explicit_preference_candidate(
        user_id="user-1",
        request_id="00000000-0000-4000-8000-000000000009",
        preference_key="style",
        preference_value="private value",
    )
    dependencies.memory_gate = lambda **kwargs: [
        MemoryDecision(candidate=candidate, decision="approved", reason_code="policy_approved")
    ]

    result = Phase2TutorEngine(dependencies).run(
        TutorRunRequest(
            trigger_type="chat",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            user_message="hello",
            memory_candidates=[candidate],
        )
    )

    payload = result.workflow_actions[-1].audit_payload["memory_gate"]
    serialized = repr(payload)
    assert payload["policy_version"] == "memory-gate-v1"
    assert payload["items"] == [
        {
            "candidate_id": candidate.candidate_id,
            "origin": "explicit_user_statement",
            "decision": "approved",
            "reason_code": "policy_approved",
        }
    ]
    assert "private value" not in serialized
    assert "idempotency" not in serialized
    assert "content_hash" not in serialized
