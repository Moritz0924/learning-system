import ast
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, get_type_hints
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from adaptive_tutor.tutor.memory import CreateMemoryCommand, MemoryCandidate, MemoryPrivacySettings
from adaptive_tutor.tutor.contracts import (
    TutorAssessmentRepository,
    TutorLlmClient,
    TutorMemoryGate,
    TutorRagRepository,
    TutorStateRepository,
)
from adaptive_tutor.tutor.identifiers import stable_request_hash
from adaptive_tutor.tutor.models import (
    ConversationState,
    EvidenceState,
    ExecutionState,
    LearningState,
    TutorWorkflowState,
)
from adaptive_tutor.tutor.services import (
    AssessmentService,
    GroundingService,
    IntentRouter,
    PlanningService,
    SessionContextService,
    WorkflowPersistenceService,
)
from adaptive_tutor.tutor.state import LegacyTutorStateAdapter
from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.schemas import (
    AssessmentAttemptResult,
    AssessmentDraft,
    MasteryUpdate,
    RetrievedChunk,
    TutorContext,
    TutorRunRequest,
)
from backend.app.domain.memory import MemoryCandidate as LegacyMemoryCandidate


ROOT = Path(__file__).resolve().parents[2]


def test_shared_memory_contracts_preserve_backend_import_compatibility():
    assert LegacyMemoryCandidate is MemoryCandidate
    assert MemoryPrivacySettings().enabled is True


def test_shared_memory_contract_rejects_backslash_in_idempotency_key():
    with pytest.raises(ValidationError):
        CreateMemoryCommand(
            user_id="user-1",
            memory_type="learning_preference",
            content={"style": "examples"},
            source_kind="explicit_user",
            idempotency_key=r"candidate\key",
        )


def test_workflow_state_groups_conversation_learning_evidence_and_execution():
    state = TutorWorkflowState(
        conversation=ConversationState(thread_id="thread-1", user_id="user-1", user_message="Explain RAG"),
        learning=LearningState(goal_id="goal-1", current_task={"id": "task-1"}),
        evidence=EvidenceState(retrieved_chunk_ids=["chunk-1"]),
        execution=ExecutionState(run_id="run-1", graph_version="phase2-v1"),
    )

    assert state.conversation.thread_id == "thread-1"
    assert state.learning.goal_id == "goal-1"
    assert state.evidence.retrieved_chunk_ids == ["chunk-1"]
    assert state.execution.graph_version == "phase2-v1"


def test_legacy_state_adapter_creates_and_projects_the_canonical_workflow_state():
    legacy_state = {
        "thread_id": "thread-1",
        "user_id": "user-1",
        "goal_id": "goal-1",
        "user_message": "Explain RAG",
        "active_plan": {"id": "plan-1"},
        "current_task": {"id": "task-1"},
        "mastery_snapshot": {"node-1": 80},
        "recent_learning_events": [{"event": "task_completed"}],
    }

    adapter = LegacyTutorStateAdapter()
    workflow_state = adapter.ingress(legacy_state, run_id="run-1", graph_version="phase2-v1")
    adapter.egress(legacy_state, workflow_state)

    assert legacy_state["workflow_state"] is workflow_state
    assert workflow_state.conversation.user_message == "Explain RAG"
    assert workflow_state.learning.active_plan == {"id": "plan-1"}
    assert "mastery_snapshot" not in legacy_state


def test_node_services_read_learning_inputs_only_from_workflow_state():
    dependencies = build_mock_phase2_dependencies()
    request = TutorRunRequest(
        trigger_type="assessment_due", user_id="user-1", goal_id="goal-1", thread_id="thread-1"
    )
    state = {"request": request, "audit_log": []}
    SessionContextService().load(state, dependencies)
    state.update(
        {
            "active_plan": {"id": "legacy-plan"},
            "current_task": {"knowledge_node_ids": ["legacy-node"]},
            "mastery_snapshot": {"legacy-node": {"score": 0}},
        }
    )
    built_for: list[list[str]] = []
    captured_mastery: list[object] = []
    captured_plan: list[str] = []

    AssessmentService().build_draft(
        state,
        build_assessment=lambda _kind, node_ids: built_for.append(node_ids)
        or SimpleNamespace(assessment_id="assessment-1", items=[object()]),
    )
    AssessmentService().grade_attempt(
        state,
        SimpleNamespace(assessment_repository=SimpleNamespace(get_assessment_draft=lambda _id: object())),
        grade_assessment=lambda _draft, _answers: SimpleNamespace(feedback="graded", score=100),
        mastery_updates=lambda _draft, _result, mastery: captured_mastery.append(mastery) or [],
    )
    PlanningService().plan(
        state,
        decide_action=lambda _signals: SimpleNamespace(evidence_json={}, decision="keep"),
        generate_adjustment=lambda **kwargs: captured_plan.append(kwargs["previous_plan_id"])
        or SimpleNamespace(decision="keep"),
    )

    assert built_for == [["rag_foundations"]]
    assert captured_mastery == [state["workflow_state"].learning.mastery_summary]
    assert captured_plan == ["plan-1"]


def test_domain_dependency_protocols_are_runtime_checkable_contracts():
    for contract in (TutorLlmClient, TutorRagRepository, TutorStateRepository):
        assert getattr(contract, "_is_runtime_protocol", False) is True


def test_domain_protocols_match_the_concrete_phase2_dependency_signatures():
    assert get_type_hints(TutorStateRepository.refresh_snapshot)["updates"] == dict[str, Any]
    assert get_type_hints(TutorLlmClient.complete)["tutor_context"] == TutorContext | None
    assert get_type_hints(TutorLlmClient.complete)["context"] == list[Any] | None
    assert get_type_hints(TutorAssessmentRepository.get_assessment_draft)["return"] is AssessmentDraft
    assert get_type_hints(TutorMemoryGate.__call__)["assessment_result"] == AssessmentAttemptResult | None
    assert get_type_hints(TutorMemoryGate.__call__)["mastery_updates"] == list[MasteryUpdate]


def test_stable_request_hash_normalizes_json_and_text_across_key_order():
    left = {"message": "Cafe\u0301\r\n", "metadata": {"b": 2, "a": 1}}
    right = {"metadata": {"a": 1, "b": 2}, "message": "Caf\u00e9\n"}

    assert stable_request_hash(left) == stable_request_hash(right)
    assert len(stable_request_hash(left)) == 64


def test_stable_request_hash_normalizes_mapping_keys():
    composed_key = {"Caf\u00e9\r\n": "value"}
    decomposed_key = {"Cafe\u0301\n": "value"}

    assert stable_request_hash(composed_key) == stable_request_hash(decomposed_key)


def test_stable_request_hash_rejects_normalized_mapping_key_collisions_in_any_order():
    left_first = {"Caf\u00e9": "left", "Cafe\u0301": "right"}
    right_first = {"Cafe\u0301": "right", "Caf\u00e9": "left"}

    for request in (left_first, right_first):
        with pytest.raises(ValueError, match="normalized mapping key collision"):
            stable_request_hash(request)


def test_stable_request_hash_does_not_depend_on_python_hash_seed():
    command = (
        "from adaptive_tutor.tutor.identifiers import stable_request_hash; "
        "print(stable_request_hash({'message': 'Explain RAG', 'metadata': {'b': 2, 'a': 1}}))"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
    environment["PYTHONHASHSEED"] = "1"
    first = subprocess.run(
        [sys.executable, "-c", command], check=True, capture_output=True, text=True, env=environment
    ).stdout.strip()
    environment["PYTHONHASHSEED"] = "98765"
    second = subprocess.run(
        [sys.executable, "-c", command], check=True, capture_output=True, text=True, env=environment
    ).stdout.strip()

    assert first == second


def test_uuid_run_ids_are_independent_from_the_thread_id():
    adapter = LegacyTutorStateAdapter()
    first = adapter.ingress(
        {"thread_id": "shared-thread", "user_id": "user-1", "goal_id": "goal-1"},
        graph_version="phase2-v1",
    )
    second = adapter.ingress(
        {"thread_id": "shared-thread", "user_id": "user-1", "goal_id": "goal-1"},
        graph_version="phase2-v1",
    )

    assert first.execution.run_id != second.execution.run_id
    assert first.execution.run_id != "shared-thread"
    assert UUID(first.execution.run_id).version == 4


def test_grounding_passes_answer_and_keeps_only_retrieved_candidate_citations():
    result = GroundingService().validate(
        answer="RAG retrieves evidence before generation.",
        retrieved_chunk_ids=["chunk-1", "chunk-2"],
        candidate_citation_ids=["chunk-2", "external-chunk"],
    )

    assert result.answer == "RAG retrieves evidence before generation."
    assert result.is_valid is True
    assert result.validated_citation_ids == ["chunk-2"]
    assert result.invalid_citation_ids == ["external-chunk"]


def test_intent_router_preserves_phase2_trigger_routes():
    router = IntentRouter()

    assert router.route_after_load("chat") == "retrieve_context"
    assert router.route_after_load("assessment_submitted") == "grade_assessment"
    assert router.route_after_observer(trigger_type="manual_replan", observer_decision=None) == "planner"


def test_workflow_persistence_service_emits_action_without_executing_it():
    actions = WorkflowPersistenceService().build_actions(
        state={"assessment_draft": "draft"},
        action_factory=lambda action_type, **payload: {"action_type": action_type, **payload},
    )

    assert actions == [{"action_type": "save_assessment_draft", "assessment_draft": "draft"}]


def test_shared_tutor_domain_does_not_depend_on_backend_application():
    offenders = []
    for path in (ROOT / "src" / "adaptive_tutor" / "tutor").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("backend.app"):
                offenders.append(path.name)
            if isinstance(node, ast.Import) and any(name.name.startswith("backend.app") for name in node.names):
                offenders.append(path.name)

    assert offenders == []
