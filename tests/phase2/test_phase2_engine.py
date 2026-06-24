from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.rag import ingest_markdown_document
from adaptive_tutor.phase2.schemas import TutorRunRequest


def test_chat_flow_returns_teacher_answer_with_rag_citations():
    deps = build_mock_phase2_dependencies()
    ingest_markdown_document(
        deps.rag_repository,
        filename="course.md",
        content="# RAG\nRAG retrieves document chunks before answering.",
        corpus_type="curated",
        trusted_level=3,
    )
    engine = Phase2TutorEngine(deps)

    result = engine.run(
        TutorRunRequest(
            trigger_type="chat",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            user_message="How does RAG work?",
        )
    )

    assert result.route == "teaching"
    assert result.final_answer
    assert result.citations
    assert deps.audit_sink.agent_runs[-1]["status"] == "success"


def test_assessment_due_flow_persists_draft_assessment():
    deps = build_mock_phase2_dependencies()
    engine = Phase2TutorEngine(deps)

    result = engine.run(
        TutorRunRequest(
            trigger_type="assessment_due",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            assessment_type="daily",
            knowledge_node_ids=["rag_foundations"],
        )
    )

    assert result.route == "assessment"
    assert result.assessment_draft is not None
    assert deps.assessment_repository.assessment_drafts


def test_assessment_submission_updates_mastery_and_can_trigger_replan():
    deps = build_mock_phase2_dependencies()
    draft = deps.assessment_repository.save_assessment_draft(
        deps.assessment_factory("daily", ["rag_foundations"])
    )
    engine = Phase2TutorEngine(deps)

    result = engine.run(
        TutorRunRequest(
            trigger_type="assessment_submitted",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            assessment_id=draft.assessment_id,
            submitted_answers={item.item_id: "wrong" for item in draft.items},
        )
    )

    assert result.assessment_result is not None
    assert result.mastery_updates
    assert result.observer_decision is not None
    assert deps.assessment_repository.mastery_updates


def test_manual_replan_flow_persists_plan_adjustment_and_refreshes_state():
    deps = build_mock_phase2_dependencies()
    engine = Phase2TutorEngine(deps)

    result = engine.run(
        TutorRunRequest(
            trigger_type="manual_replan",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            user_message="Please rebalance my plan this week.",
        )
    )

    assert result.route == "replan"
    assert result.plan_adjustment is not None
    assert deps.plan_repository.plan_adjustments
    assert deps.state_repository.snapshots[("user-1", "goal-1")]["latest_plan_adjustment_id"]


def test_manual_replan_uses_observer_signals_instead_of_message_keywords():
    same_message = "Please rebalance my plan based on my current progress."

    remediate_deps = build_mock_phase2_dependencies()
    remediate_deps.state_repository.snapshots[("user-1", "goal-1")] = {
        "user_id": "user-1",
        "goal_id": "goal-1",
        "active_plan": {"id": "plan-1", "version": 1},
        "current_task": {"knowledge_node_ids": ["rag_foundations"]},
        "mastery_summary": {"rag_foundations": {"score": 42, "confidence": 0.9}},
        "recent_learning_events": [],
        "observer_signals": {
            "completion_rate_7d": 0.9,
            "correctness_rate": 0.45,
            "mastery_delta": -12,
            "low_mastery_nodes": [{"knowledge_node_id": "rag_foundations", "score": 42}],
            "wrong_reason_tags": ["missing_key_concept"],
            "recent_attempts": [{"assessment_id": "assessment-1", "score": 45}],
            "missing_data_strategy": {},
        },
    }
    remediate_result = Phase2TutorEngine(remediate_deps).run(
        TutorRunRequest(
            trigger_type="manual_replan",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            user_message=same_message,
        )
    )

    advance_deps = build_mock_phase2_dependencies()
    advance_deps.state_repository.snapshots[("user-1", "goal-1")] = {
        "user_id": "user-1",
        "goal_id": "goal-1",
        "active_plan": {"id": "plan-1", "version": 1},
        "current_task": {"knowledge_node_ids": ["rag_foundations"]},
        "mastery_summary": {"rag_foundations": {"score": 91, "confidence": 0.9}},
        "recent_learning_events": [],
        "observer_signals": {
            "completion_rate_7d": 0.95,
            "correctness_rate": 0.93,
            "mastery_delta": 8,
            "low_mastery_nodes": [],
            "wrong_reason_tags": [],
            "recent_attempts": [{"assessment_id": "assessment-2", "score": 93}],
            "missing_data_strategy": {},
        },
    }
    advance_result = Phase2TutorEngine(advance_deps).run(
        TutorRunRequest(
            trigger_type="manual_replan",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            user_message=same_message,
        )
    )

    assert remediate_result.route == "replan"
    assert remediate_result.plan_adjustment is not None
    assert remediate_result.plan_adjustment.decision == "remediate"
    assert remediate_result.plan_adjustment.evidence_json["manual_request"] == same_message
    assert remediate_result.plan_adjustment.evidence_json["observer_signals"]["correctness_rate"] == 0.45

    assert advance_result.route == "replan"
    assert advance_result.plan_adjustment is not None
    assert advance_result.plan_adjustment.decision == "advance"
    assert advance_result.plan_adjustment.evidence_json["manual_request"] == same_message
    assert advance_result.plan_adjustment.evidence_json["observer_signals"]["correctness_rate"] == 0.93
