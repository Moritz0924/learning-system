from adaptive_tutor.phase2.assessment import (
    build_assessment_draft,
    calculate_mastery_update,
    grade_assessment_attempt,
)
from adaptive_tutor.phase2.mocks import MockEmbeddingClient, MockOCRClient
from adaptive_tutor.phase2.rag import InMemoryRagRepository, ingest_markdown_document
from adaptive_tutor.phase2.replanning import (
    build_observer_signals,
    decide_observer_action,
    decide_observer_action_from_signals,
    generate_plan_adjustment,
)


def test_markdown_ingestion_marks_user_uploads_untrusted_and_searchable():
    repository = InMemoryRagRepository(embedding_client=MockEmbeddingClient())

    document = ingest_markdown_document(
        repository,
        filename="rag-notes.md",
        content="# RAG\nRetrieval augmented generation cites trusted chunks.",
        corpus_type="user_uploaded",
        owner_user_id="user-1",
        trusted_level=1,
    )
    results = repository.retrieve("trusted chunks", user_id="user-1", top_k=1)

    assert document.parse_status == "success"
    assert results[0].document_id == document.document_id
    assert results[0].trusted_level == 1
    assert results[0].metadata["untrusted_input"] is True
    assert results[0].citation_label.startswith("rag-notes.md chunk")


def test_mock_ocr_output_can_be_ingested_as_distinct_source_type():
    repository = InMemoryRagRepository(embedding_client=MockEmbeddingClient())
    ocr_text = MockOCRClient().extract_text(b"image-bytes", filename="notes.png")

    ingest_markdown_document(
        repository,
        filename="notes.png",
        content=ocr_text,
        corpus_type="user_uploaded",
        owner_user_id="user-1",
        trusted_level=1,
        source_type="ocr",
    )
    result = repository.retrieve("image", user_id="user-1", top_k=1)[0]

    assert result.metadata["source_type"] == "ocr"
    assert result.metadata["untrusted_input"] is True


def test_retrieval_scopes_user_uploads_but_keeps_curated_sources():
    repository = InMemoryRagRepository(embedding_client=MockEmbeddingClient())
    curated = ingest_markdown_document(
        repository,
        filename="curated-rag.md",
        content="Curated retrieval guidance for every learner.",
        corpus_type="curated",
        trusted_level=4,
    )
    own = ingest_markdown_document(
        repository,
        filename="own-notes.md",
        content="Private upload about worker citations.",
        corpus_type="user_uploaded",
        owner_user_id="user-1",
        trusted_level=1,
    )
    other = ingest_markdown_document(
        repository,
        filename="other-notes.md",
        content="Private upload from another learner.",
        corpus_type="user_uploaded",
        owner_user_id="user-2",
        trusted_level=1,
    )

    results = repository.retrieve("private upload retrieval guidance", user_id="user-1", top_k=10)

    document_ids = {result.document_id for result in results}
    assert own.document_id in document_ids
    assert curated.document_id in document_ids
    assert other.document_id not in document_ids


def test_assessment_builder_uses_expected_item_counts():
    daily = build_assessment_draft("daily", ["rag_foundations"])
    weekly = build_assessment_draft("weekly", ["rag_foundations", "langgraph_basics"])
    phase = build_assessment_draft("phase", ["rag_foundations"])

    assert 3 <= len(daily.items) <= 5
    assert 10 <= len(weekly.items) <= 15
    assert len(phase.items) >= 1
    assert daily.status == "draft"


def test_mastery_update_clamps_decay_and_records_missing_data_strategy():
    update = calculate_mastery_update(
        knowledge_node_id="rag_foundations",
        previous_score=98,
        recent_assessment_score=120,
        explanation_score=None,
        task_independence_score=None,
        days_since_practice=100,
        evidence_count=1,
    )

    assert 0 <= update.new_score <= 100
    assert update.source_breakdown["forgetting_decay"] == 15
    assert update.missing_data_strategy["explanation_score"] == "defaulted_to_60"
    assert update.confidence < 1


def test_observer_decisions_map_to_plan_adjustments():
    reduce = decide_observer_action(completion_rate_7d=0.4, correctness_rate=0.8, mastery_delta=0)
    remediate = decide_observer_action(completion_rate_7d=0.9, correctness_rate=0.45, mastery_delta=-12)
    advance = decide_observer_action(completion_rate_7d=0.95, correctness_rate=0.93, mastery_delta=8)
    keep = decide_observer_action(completion_rate_7d=0.8, correctness_rate=0.8, mastery_delta=1)

    assert reduce.decision == "reduce"
    assert remediate.decision == "remediate"
    assert advance.decision == "advance"
    assert keep.decision == "keep"

    adjustment = generate_plan_adjustment(
        user_id="user-1",
        goal_id="goal-1",
        previous_plan_id="plan-1",
        decision=remediate,
    )

    assert adjustment.decision == "remediate"
    assert "change_summary" in adjustment.model_dump()
    assert adjustment.rationale_json["decision"] == "remediate"


def test_observer_signals_default_missing_data_and_drive_decisions():
    neutral = build_observer_signals(
        completion_rate_7d=None,
        correctness_rate=None,
        mastery_delta=None,
        low_mastery_nodes=[],
        wrong_reason_tags=[],
        recent_attempts=[],
    )

    assert neutral["completion_rate_7d"] == 0.8
    assert neutral["correctness_rate"] == 0.8
    assert neutral["mastery_delta"] == 0
    assert neutral["missing_data_strategy"] == {
        "completion_rate_7d": "defaulted_to_0.8",
        "correctness_rate": "defaulted_to_0.8",
        "mastery_delta": "defaulted_to_0",
    }
    assert decide_observer_action_from_signals(neutral).decision == "keep"

    remediate = build_observer_signals(
        completion_rate_7d=0.9,
        correctness_rate=0.45,
        mastery_delta=-12,
        low_mastery_nodes=[{"knowledge_node_id": "rag_foundations", "score": 42}],
        wrong_reason_tags=["missing_key_concept"],
        recent_attempts=[{"assessment_id": "assessment-1", "score": 45}],
    )

    decision = decide_observer_action_from_signals(remediate)

    assert decision.decision == "remediate"
    assert decision.evidence_json["low_mastery_nodes"][0]["knowledge_node_id"] == "rag_foundations"
    assert decision.evidence_json["wrong_reason_tags"] == ["missing_key_concept"]
    assert decision.evidence_json["recent_attempts"][0]["score"] == 45


def test_grading_attempt_produces_feedback_and_wrong_reason_tags():
    draft = build_assessment_draft("daily", ["rag_foundations"])
    result = grade_assessment_attempt(
        draft,
        answers={item.item_id: "I am not sure" for item in draft.items},
    )

    assert result.status == "graded"
    assert result.score < 100
    assert result.answers[0].grader_type == "rule"
    assert result.answers[0].evidence_json["wrong_reason_tags"]
