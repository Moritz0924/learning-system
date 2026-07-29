import ast
from pathlib import Path

from adaptive_tutor.tutor.memory import MemoryCandidate, MemoryPrivacySettings
from adaptive_tutor.tutor.models import (
    ConversationState,
    EvidenceState,
    ExecutionState,
    LearningState,
    TutorWorkflowState,
)
from adaptive_tutor.tutor.services import GroundingService, IntentRouter, WorkflowPersistenceService
from backend.app.domain.memory import MemoryCandidate as LegacyMemoryCandidate


ROOT = Path(__file__).resolve().parents[2]


def test_shared_memory_contracts_preserve_backend_import_compatibility():
    assert LegacyMemoryCandidate is MemoryCandidate
    assert MemoryPrivacySettings().enabled is True


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
