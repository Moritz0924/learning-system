import ast
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest
from pydantic import ValidationError

from adaptive_tutor.tutor.memory import CreateMemoryCommand, MemoryCandidate, MemoryPrivacySettings
from adaptive_tutor.tutor.contracts import (
    TutorLlmClient,
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
from adaptive_tutor.tutor.services import GroundingService, IntentRouter, WorkflowPersistenceService
from adaptive_tutor.tutor.state import LegacyTutorStateAdapter
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
    assert legacy_state["mastery_snapshot"] == {"node-1": 80}


def test_domain_dependency_protocols_are_runtime_checkable_contracts():
    for contract in (TutorLlmClient, TutorRagRepository, TutorStateRepository):
        assert getattr(contract, "_is_runtime_protocol", False) is True


def test_stable_request_hash_normalizes_json_and_text_across_key_order():
    left = {"message": "Cafe\u0301\r\n", "metadata": {"b": 2, "a": 1}}
    right = {"metadata": {"a": 1, "b": 2}, "message": "Caf\u00e9\n"}

    assert stable_request_hash(left) == stable_request_hash(right)
    assert len(stable_request_hash(left)) == 64


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
