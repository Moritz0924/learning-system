from __future__ import annotations

from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2 import schemas
from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.schemas import MemoryContextSelection, TutorMemoryContext, TutorRunRequest
from backend.app.application import memory_context_service


def test_application_builder_adds_selected_memories_and_preserves_existing_tutor_context():
    snapshot = {
        "learning_goal": {
            "goal_id": "goal-1",
            "title": "Build AI apps",
            "target_outcome": "Ship a grounded tutor",
            "domain": "ai_app_dev",
            "deadline": None,
            "weekly_hours_target": 8,
        },
        "current_task": {
            "task_id": "task-1",
            "title": "RAG foundations",
            "objective": "Explain retrieval before generation",
            "task_type": "study",
            "knowledge_node_id": "rag_foundations",
            "estimated_minutes": 45,
            "status": "active",
        },
        "mastery_summary": {
            "other-node": {"score": 35, "confidence": 0.7, "evidence_count": 2},
            "rag_foundations": {"score": 88, "confidence": 0.9, "evidence_count": 5},
        },
        "learning_preferences": {"style": "examples_first"},
        "recent_learning_events": [],
    }
    memory = TutorMemoryContext(
        memory_id="memory-1",
        memory_type="learning_preference",
        scope="user",
        content={"preference_key": "tone", "preference_value": "concise"},
        importance=0.8,
        confidence=0.9,
        source_kind="explicit_user",
        expires_at=None,
    )
    selection = MemoryContextSelection(
        items=[memory],
        selected_memory_ids=[memory.memory_id],
        serialized_char_count=300,
    )

    assert hasattr(memory_context_service, "build_tutor_context")
    context = memory_context_service.build_tutor_context(snapshot, memory_selection=selection)

    assert context.learning_goal.goal_id == "goal-1"
    assert context.current_task is not None
    assert context.current_task.knowledge_node_id == "rag_foundations"
    assert context.mastery_summary[0].knowledge_node_id == "rag_foundations"
    assert context.learning_preferences == {"style": "examples_first"}
    assert context.long_term_memories == [memory]


def test_phase2_engine_no_longer_owns_tutor_context_construction_helpers():
    assert not hasattr(Phase2TutorEngine, "_build_tutor_context")
    assert not hasattr(Phase2TutorEngine, "_tutor_mastery_summary")


class _UnexpectedStateRepository:
    def load_context(self, user_id: str, goal_id: str) -> dict:
        raise AssertionError("prepared chat must not read state through the engine")


class _UnexpectedRagRepository:
    def retrieve(self, query: str, *, top_k: int = 5, user_id: str | None = None):
        raise AssertionError("prepared chat must not retrieve RAG through the engine")


class _CapturingLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(
        self,
        *,
        role: str,
        prompt: str,
        tutor_context=None,
        conversation_context=None,
        context=None,
    ) -> str:
        self.calls.append(
            {
                "role": role,
                "prompt": prompt,
                "tutor_context": tutor_context,
                "conversation_context": conversation_context,
                "context": list(context or []),
            }
        )
        return "Prepared answer"


def test_phase2_chat_consumes_detached_prepared_context_without_repository_reads():
    assert hasattr(schemas, "PreparedTutorContext")
    snapshot = {
        "learning_goal": {
            "goal_id": "goal-1",
            "title": "Build AI apps",
            "target_outcome": "Ship a grounded tutor",
            "domain": "ai_app_dev",
            "deadline": None,
            "weekly_hours_target": 8,
        },
        "current_task": None,
        "mastery_summary": {},
        "learning_preferences": {},
        "recent_learning_events": [],
        "active_plan": {"id": "plan-1", "version": 1},
        "observer_signals": {},
    }
    memory = TutorMemoryContext(
        memory_id="memory-1",
        memory_type="learning_preference",
        scope="user",
        content={"preference_key": "style", "preference_value": "examples_first"},
        importance=0.8,
        confidence=0.9,
        source_kind="explicit_user",
        expires_at=None,
    )
    selection = MemoryContextSelection(
        items=[memory],
        selected_memory_ids=[memory.memory_id],
        serialized_char_count=300,
    )
    chunk = schemas.RetrievedChunk(
        chunk_id="chunk-1",
        document_id="document-1",
        content="Retrieved evidence",
        citation_label="Course Notes",
        trusted_level=3,
    )
    prepared = schemas.PreparedTutorContext(
        state_snapshot=snapshot,
        tutor_context=memory_context_service.build_tutor_context(
            snapshot,
            memory_selection=selection,
        ),
        retrieved_context=[chunk],
        retrieval_status="grounded",
        degraded_reason=None,
        embedding_provider="deterministic_test",
        retrieval_backend="local_json_embedding",
        memory_selection=selection,
    )
    dependencies = build_mock_phase2_dependencies()
    dependencies.state_repository = _UnexpectedStateRepository()
    dependencies.rag_repository = _UnexpectedRagRepository()
    capture = _CapturingLLM()
    dependencies.llm_client = capture

    result = Phase2TutorEngine(dependencies).run(
        TutorRunRequest(
            trigger_type="chat",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            user_message="Explain RAG.",
        ),
        prepared_context=prepared,
    )

    assert result.final_answer == "Prepared answer"
    assert result.citations == [chunk]
    assert capture.calls[0]["tutor_context"].long_term_memories == [memory]
    assert capture.calls[0]["conversation_context"] is None
    assert result.workflow_actions[-1].audit_payload["memory_context"] == {
        "selected_memory_ids": ["memory-1"],
        "policy_version": "memory-context-v1",
    }
