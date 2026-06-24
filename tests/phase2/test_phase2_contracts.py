from pydantic import ValidationError

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
