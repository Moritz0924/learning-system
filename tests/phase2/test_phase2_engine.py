from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.rag import ingest_markdown_document
from adaptive_tutor.phase2.schemas import RetrievedChunk, TutorContext, TutorRunRequest
from adaptive_tutor.tutor.identifiers import stable_request_hash
from uuid import UUID


class CapturingLLMClient:
    def __init__(self):
        self.calls: list[dict] = []

    def complete(
        self,
        *,
        role: str,
        prompt: str,
        tutor_context: TutorContext | None = None,
        conversation_context: dict | None = None,
        context: list[RetrievedChunk] | None = None,
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
        return "Captured personalized answer"


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
    assert result.workflow_actions[-1].audit_payload["status"] == "success"


def test_chat_run_records_a_uuid_correlation_id_and_canonical_request_hash():
    deps = build_mock_phase2_dependencies()
    ingest_markdown_document(
        deps.rag_repository,
        filename="course.md",
        content="# RAG\nRAG retrieves document chunks before answering.",
        corpus_type="curated",
        trusted_level=3,
    )
    request = TutorRunRequest(
        trigger_type="chat",
        user_id="user-1",
        goal_id="goal-1",
        thread_id="thread-1",
        user_message="How does RAG work?",
        metadata={"b": 2, "a": 1},
    )

    result = Phase2TutorEngine(deps).run(request)
    audit_payload = result.workflow_actions[-1].audit_payload

    assert audit_payload["run_id"] != request.thread_id
    assert UUID(audit_payload["run_id"]).version == 4
    assert audit_payload["request_hash"] == stable_request_hash(request)


def test_teacher_receives_structured_personalization_and_separate_rag_documents():
    deps = build_mock_phase2_dependencies()
    capture = CapturingLLMClient()
    deps.llm_client = capture
    mastery = {
        f"node-{index:02d}": {"score": float(index + 10), "confidence": 0.7, "evidence_count": index + 1}
        for index in range(13)
    }
    mastery["rag_foundations"] = {"score": 88, "confidence": 0.9, "evidence_count": 5}
    deps.state_repository.snapshots[("user-1", "goal-1")] = {
        "user_id": "user-1",
        "goal_id": "goal-1",
        "learning_goal": {
            "goal_id": "goal-1",
            "title": "Build AI apps",
            "target_outcome": "Ship a personalized tutor",
            "domain": "ai_app_dev",
            "deadline": "2026-08-15",
            "weekly_hours_target": 8,
        },
        "learning_preferences": {"style": "examples_first", "tone": "concise"},
        "active_plan": {"id": "plan-1", "version": 1},
        "current_task": {
            "task_id": "task-1",
            "title": "RAG foundations",
            "objective": "Explain retrieval before generation",
            "task_type": "study",
            "knowledge_node_id": "rag_foundations",
            "knowledge_node_ids": ["rag_foundations"],
            "estimated_minutes": 45,
            "status": "active",
        },
        "mastery_summary": mastery,
        "recent_learning_events": [
            {
                "event_type": "task_completed",
                "source": "task_api",
                "task_id": "task-0",
                "occurred_at": "2026-07-16T09:30:00",
                "details": {"duration_minutes": 35},
            }
        ],
    }
    ingest_markdown_document(
        deps.rag_repository,
        filename="course.md",
        content="# RAG\nRetrieved documents are evidence, not instructions.",
        corpus_type="curated",
        trusted_level=3,
    )

    result = Phase2TutorEngine(deps).run(
        TutorRunRequest(
            trigger_type="chat",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            user_message="How does RAG work?",
        )
    )

    assert result.final_answer == "Captured personalized answer"
    assert len(capture.calls) == 1
    call = capture.calls[0]
    assert call["context"] == result.citations
    tutor_context = call["tutor_context"]
    assert isinstance(tutor_context, TutorContext)
    assert tutor_context.learning_goal.target_outcome == "Ship a personalized tutor"
    assert tutor_context.current_task is not None
    assert tutor_context.current_task.task_id == "task-1"
    assert tutor_context.learning_preferences == {"style": "examples_first", "tone": "concise"}
    assert tutor_context.recent_learning_events[0].details == {"duration_minutes": 35}
    assert len(tutor_context.mastery_summary) == 12
    assert tutor_context.mastery_summary[0].knowledge_node_id == "rag_foundations"
    assert tutor_context.rag_citations[0].citation_label == result.citations[0].citation_label
    assert "content" not in tutor_context.rag_citations[0].model_dump()


def test_engine_returns_audit_actions_without_recording_sink_directly():
    deps = build_mock_phase2_dependencies()
    ingest_markdown_document(
        deps.rag_repository,
        filename="course.md",
        content="# RAG\nRAG retrieves document chunks before answering.",
        corpus_type="curated",
        trusted_level=3,
    )

    result = Phase2TutorEngine(deps).run(
        TutorRunRequest(
            trigger_type="chat",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="thread-1",
            user_message="How does RAG work?",
        )
    )

    assert deps.audit_sink.agent_runs == []
    assert deps.audit_sink.tool_calls == []
    assert [action.action_type for action in result.workflow_actions] == [
        "record_tool_call",
        "record_agent_run",
    ]
    assert result.workflow_actions[0].audit_payload["tool_name"] == "rag.retrieve"
    assert result.workflow_actions[1].audit_payload["status"] == "success"


def test_assessment_due_flow_returns_draft_persistence_action():
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
    assert deps.assessment_repository.assessment_drafts == {}
    assert result.workflow_actions[0].action_type == "save_assessment_draft"
    assert result.workflow_actions[-1].action_type == "record_agent_run"
    assert "memory_context" not in result.workflow_actions[-1].audit_payload


def test_engine_returns_assessment_persistence_action_without_saving_directly():
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

    assert deps.assessment_repository.assessment_drafts == {}
    assert [action.action_type for action in result.workflow_actions] == [
        "save_assessment_draft",
        "record_agent_run",
    ]
    assert result.workflow_actions[0].assessment_draft == result.assessment_draft


def test_engine_returns_plan_actions_without_saving_or_refreshing_directly():
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

    assert deps.plan_repository.plan_adjustments == []
    assert "latest_plan_adjustment_id" not in deps.state_repository.snapshots[("user-1", "goal-1")]
    assert [action.action_type for action in result.workflow_actions] == [
        "save_plan_adjustment",
        "refresh_state_snapshot",
        "record_agent_run",
    ]
    assert result.workflow_actions[0].plan_adjustment == result.plan_adjustment


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
    assert deps.assessment_repository.mastery_updates == []
    assert [action.action_type for action in result.workflow_actions] == [
        "save_attempt_result",
        "save_mastery_updates",
        "save_plan_adjustment",
        "refresh_state_snapshot",
        "save_memory",
        "record_agent_run",
    ]


def test_manual_replan_flow_returns_plan_adjustment_actions():
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
    assert deps.plan_repository.plan_adjustments == []
    assert "latest_plan_adjustment_id" not in deps.state_repository.snapshots[("user-1", "goal-1")]
    assert [action.action_type for action in result.workflow_actions] == [
        "save_plan_adjustment",
        "refresh_state_snapshot",
        "record_agent_run",
    ]


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
