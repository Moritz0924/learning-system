from datetime import date, datetime
from inspect import signature

import pytest
from pydantic import ValidationError

import adaptive_tutor.phase2 as phase2
from adaptive_tutor.phase2 import schemas
from adaptive_tutor.phase2.mocks import MockLLMClient
from adaptive_tutor.phase2.ports import LLMClient

from adaptive_tutor.phase2.schemas import (
    AssessmentDraft,
    MasteryUpdate,
    ObserverDecision,
    PlanAdjustment,
    RetrievedChunk,
    TutorRunRequest,
)


def test_run_request_accepts_frozen_trigger_types():
    request = TutorRunRequest(
        trigger_type="chat",
        user_id="user-1",
        goal_id="goal-1",
        thread_id="thread-1",
        user_message="Explain RAG.",
    )

    assert request.trigger_type == "chat"


def test_run_request_rejects_unknown_trigger_type():
    try:
        TutorRunRequest(
            trigger_type="unsupported",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
        )
    except ValidationError as exc:
        assert "trigger_type" in str(exc)
    else:
        raise AssertionError("unknown trigger type should fail validation")


def test_retrieved_chunk_preserves_source_and_citation_fields():
    chunk = RetrievedChunk(
        chunk_id="chunk-1",
        document_id="doc-1",
        content="LangGraph controls state transitions.",
        citation_label="Course Notes p.1",
        source_title="Course Notes",
        source_url="https://example.test/course",
        trusted_level=2,
        metadata={"page": 1, "source_type": "markdown"},
    )

    assert chunk.citation_label == "Course Notes p.1"
    assert chunk.trusted_level == 2
    assert chunk.metadata["source_type"] == "markdown"


def test_tutor_context_exposes_structured_personalization_without_rag_content():
    assert hasattr(schemas, "TutorContext")
    assert phase2.TutorContext is schemas.TutorContext

    context = schemas.TutorContext(
        learning_goal={
            "goal_id": "goal-1",
            "title": "Build AI apps",
            "target_outcome": "Ship a grounded tutor",
            "domain": "ai_app_dev",
            "deadline": date(2026, 8, 15),
            "weekly_hours_target": 8,
        },
        current_task={
            "task_id": "task-1",
            "title": "RAG foundations",
            "objective": "Explain retrieval before generation",
            "task_type": "study",
            "knowledge_node_id": "rag_foundations",
            "estimated_minutes": 45,
            "status": "active",
        },
        mastery_summary=[
            {
                "knowledge_node_id": "rag_foundations",
                "score": 62,
                "confidence": 0.8,
                "evidence_count": 3,
            }
        ],
        learning_preferences={"style": "examples_first"},
        recent_learning_events=[
            {
                "event_type": "task_completed",
                "source": "task_api",
                "task_id": "task-0",
                "occurred_at": datetime(2026, 7, 16, 9, 30),
                "details": {"duration_minutes": 35},
            }
        ],
        rag_citations=[
            {
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "citation_label": "Course Notes p.1",
                "source_title": "Course Notes",
                "source_url": None,
                "trusted_level": 2,
            }
        ],
    )

    assert context.learning_goal.goal_id == "goal-1"
    assert context.current_task is not None
    assert context.current_task.knowledge_node_id == "rag_foundations"
    assert context.mastery_summary[0].score == 62
    assert "content" not in context.rag_citations[0].model_dump()


def test_tutor_context_rejects_unknown_fields_and_uses_empty_collection_defaults():
    goal = {
        "goal_id": "goal-1",
        "title": "Build AI apps",
        "target_outcome": "Ship a grounded tutor",
        "domain": "ai_app_dev",
        "deadline": None,
        "weekly_hours_target": 8,
    }

    context = schemas.TutorContext(learning_goal=goal)

    assert context.current_task is None
    assert context.mastery_summary == []
    assert context.learning_preferences == {}
    assert context.recent_learning_events == []
    assert context.rag_citations == []

    with pytest.raises(ValidationError):
        schemas.TutorContext(learning_goal=goal, unexpected="not allowed")


def test_llm_protocol_and_mock_accept_optional_tutor_context():
    assert "tutor_context" in signature(LLMClient.complete).parameters
    assert "tutor_context" in signature(MockLLMClient.complete).parameters
    assert "conversation_context" in signature(LLMClient.complete).parameters
    assert "conversation_context" in signature(MockLLMClient.complete).parameters


def test_structured_outputs_expose_audit_ready_fields():
    draft = AssessmentDraft(
        assessment_id="assessment-1",
        assessment_type="daily",
        status="draft",
        scope={"knowledge_node_ids": ["rag_foundations"]},
        items=[
            {
                "item_id": "item-1",
                "knowledge_node_id": "rag_foundations",
                "question_type": "explain",
                "prompt": "What problem does RAG solve?",
                "reference_answer": "It grounds answers in retrieved sources.",
                "rubric_json": {"max_score": 100},
                "difficulty": 2,
                "source_chunk_ids": ["chunk-1"],
            }
        ],
    )
    mastery = MasteryUpdate(
        knowledge_node_id="rag_foundations",
        previous_score=50,
        new_score=62,
        confidence=0.8,
        evidence_count=3,
        calculation_version="phase2-mastery-v1",
        source_breakdown={"recent_assessment": 80},
        missing_data_strategy={},
    )
    decision = ObserverDecision(
        decision="advance",
        evidence_json={"recent_score": 92},
        rationale="Performance is consistently high.",
    )
    adjustment = PlanAdjustment(
        trigger_type="assessment",
        decision="advance",
        status="proposed",
        plan_patch={"unlock": ["langgraph_basics"]},
        change_summary={"added": ["langgraph_basics"]},
        rationale_json={"reason": "phase assessment passed"},
    )

    assert draft.items[0].source_chunk_ids == ["chunk-1"]
    assert mastery.calculation_version == "phase2-mastery-v1"
    assert decision.decision == "advance"
    assert adjustment.change_summary["added"] == ["langgraph_basics"]
