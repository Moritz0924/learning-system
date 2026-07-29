from __future__ import annotations

from typing import Any

import pytest

from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.schemas import TutorContext, TutorMemoryContext, TutorRunRequest
from backend.app.application.conversation_service import ConversationService
from backend.app.infrastructure.checkpoints import (
    CheckpointConfigurationError,
    CheckpointSettings,
    HistoryPolicy,
    InMemoryTutorCheckpointRuntime,
    PostgresTutorCheckpointRuntime,
    build_checkpoint_runtime,
)
from backend.app.models import LearningGoal, User


class _CapturingLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        role: str,
        prompt: str,
        tutor_context: TutorContext | None = None,
        conversation_context: dict[str, Any] | None = None,
        context: list | None = None,
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
        return f"answer:{prompt}"


def _engine(
    runtime: InMemoryTutorCheckpointRuntime,
    *,
    history_policy: HistoryPolicy | None = None,
) -> tuple[Phase2TutorEngine, _CapturingLLM]:
    dependencies = build_mock_phase2_dependencies()
    llm = _CapturingLLM()
    dependencies.llm_client = llm
    return (
        Phase2TutorEngine(
            dependencies,
            checkpointer=runtime.saver,
            history_policy=history_policy,
        ),
        llm,
    )


def _request(
    thread_id: str,
    message: str,
    *,
    user_id: str = "user-1",
    goal_id: str = "goal-1",
) -> TutorRunRequest:
    return TutorRunRequest(
        trigger_type="chat",
        user_id=user_id,
        goal_id=goal_id,
        thread_id=thread_id,
        user_message=message,
    )


def _checkpoint_workflow(runtime: InMemoryTutorCheckpointRuntime, thread_id: str):
    saved = runtime.saver.get_tuple({"configurable": {"thread_id": thread_id}})
    assert saved is not None
    return saved.checkpoint["channel_values"]["workflow_state"]


def test_checkpoint_settings_select_memory_only_for_tests_and_keep_default_history_limits() -> None:
    settings = CheckpointSettings.from_mapping(
        {
            "APP_ENV": "test",
            "TUTOR_CHECKPOINT_BACKEND": "memory",
        }
    )

    runtime = build_checkpoint_runtime(settings)

    assert isinstance(runtime, InMemoryTutorCheckpointRuntime)
    assert settings.history_policy == HistoryPolicy(
        max_turns=12,
        max_estimated_tokens=16_000,
    )

    customized = CheckpointSettings.from_mapping(
        {
            "APP_ENV": "test",
            "TUTOR_HISTORY_MAX_TURNS": "14",
            "TUTOR_HISTORY_MAX_ESTIMATED_TOKENS": "2048",
        }
    )
    assert customized.history_policy == HistoryPolicy(
        max_turns=14,
        max_estimated_tokens=2_048,
    )


def test_checkpoint_settings_require_postgres_in_production_and_normalize_database_url() -> None:
    settings = CheckpointSettings.from_mapping(
        {
            "APP_ENV": "production",
            "TUTOR_CHECKPOINT_BACKEND": "postgres",
            "DATABASE_URL": (
                "postgresql+psycopg://app:secret@postgres:5432/adaptive_tutor"
            ),
        }
    )

    assert settings.database_url == (
        "postgresql://app:secret@postgres:5432/adaptive_tutor"
    )
    assert isinstance(
        build_checkpoint_runtime(settings),
        PostgresTutorCheckpointRuntime,
    )

    with pytest.raises(CheckpointConfigurationError):
        CheckpointSettings.from_mapping(
            {
                "APP_ENV": "production",
                "TUTOR_CHECKPOINT_BACKEND": "memory",
            }
        )
    with pytest.raises(CheckpointConfigurationError):
        CheckpointSettings.from_mapping(
            {
                "APP_ENV": "development",
                "TUTOR_CHECKPOINT_BACKEND": "memory",
            }
        )


def test_checkpoint_serializer_restores_registered_workflow_models_without_warning(
    caplog,
) -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    first, _ = _engine(runtime)
    first.run(_request("registered-model", "first"))

    restarted, _ = _engine(runtime)
    restarted.run(_request("registered-model", "second"))

    assert "unregistered type adaptive_tutor.tutor.models" not in caplog.text


def test_in_memory_checkpoint_restores_history_across_engine_recreation_and_isolates_threads() -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    first_engine, first_llm = _engine(runtime)
    first_engine.run(_request("thread-a", "first question"))
    assert first_llm.calls[0]["conversation_context"] is None

    restarted_engine, restarted_llm = _engine(runtime)
    restarted_engine.run(_request("thread-a", "follow up"))
    restarted_engine.run(_request("thread-b", "unrelated question"))

    restored = restarted_llm.calls[0]["conversation_context"]
    assert restored == {
        "summary": "",
        "turns": [
            {
                "user_message": "first question",
                "assistant_message": "answer:first question",
            }
        ],
    }
    assert restarted_llm.calls[1]["conversation_context"] is None


def test_completed_turn_checkpoint_can_be_deferred_until_application_commit() -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    engine, _ = _engine(runtime)
    request = _request("deferred-turn", "commit me first")

    result = engine.run(request, defer_history_checkpoint=True)

    before_commit = _checkpoint_workflow(runtime, "deferred-turn").conversation
    assert before_commit.recent_turns == []
    engine.finalize_chat_history(
        request,
        assistant_message=result.final_answer,
    )
    after_commit = _checkpoint_workflow(runtime, "deferred-turn").conversation
    assert [item.user_message for item in after_commit.recent_turns] == [
        "commit me first"
    ]


def test_checkpoint_restore_rejects_history_when_thread_ownership_does_not_match() -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    first_engine, _ = _engine(runtime)
    first_engine.run(_request("shared-thread", "private first question"))

    second_engine, second_llm = _engine(runtime)
    second_engine.run(
        _request(
            "shared-thread",
            "different owner",
            user_id="user-2",
            goal_id="goal-2",
        )
    )

    assert second_llm.calls[0]["conversation_context"] is None
    workflow = _checkpoint_workflow(runtime, "shared-thread")
    assert workflow.conversation.user_id == "user-2"
    assert workflow.learning.goal_id == "goal-2"
    assert "private first question" not in repr(workflow)


def test_checkpoint_excludes_ephemeral_context_and_long_term_memory() -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    dependencies = build_mock_phase2_dependencies()
    llm = _CapturingLLM()
    dependencies.llm_client = llm
    original_context_factory = dependencies.tutor_context_factory

    def context_with_long_term_memory(snapshot: dict) -> TutorContext:
        context = original_context_factory(snapshot)
        return context.model_copy(
            update={
                "long_term_memories": [
                    TutorMemoryContext(
                        memory_id="memory-secret-id",
                        memory_type="learning_preference",
                        scope="user",
                        content={
                            "preference_key": "explanation_style",
                            "preference_value": "private-memory-marker",
                        },
                        importance=0.8,
                        confidence=1.0,
                        source_kind="explicit_user",
                    )
                ]
            }
        )

    dependencies.tutor_context_factory = context_with_long_term_memory
    engine = Phase2TutorEngine(dependencies, checkpointer=runtime.saver)
    engine.run(
        TutorRunRequest(
            trigger_type="chat",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="safe-thread",
            user_message="ordinary history",
            metadata={"ephemeral_secret": "request-only-marker"},
        )
    )

    saved = runtime.saver.get_tuple(
        {"configurable": {"thread_id": "safe-thread"}}
    )
    assert saved is not None
    channel_values = saved.checkpoint["channel_values"]
    assert set(channel_values) == {"workflow_state"}
    serialized_checkpoint = repr(channel_values)
    assert "private-memory-marker" not in serialized_checkpoint
    assert "memory-secret-id" not in serialized_checkpoint
    assert "request-only-marker" not in serialized_checkpoint
    assert llm.calls[0]["tutor_context"].long_term_memories[0].memory_id == (
        "memory-secret-id"
    )


def test_history_compacts_at_turn_boundary_and_restores_summary_without_raw_turns() -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    policy = HistoryPolicy(max_turns=2, max_estimated_tokens=16_000)
    engine, llm = _engine(runtime, history_policy=policy)

    engine.run(_request("compact-turns", "question one"))
    first = _checkpoint_workflow(runtime, "compact-turns").conversation
    assert first.conversation_summary == ""
    assert len(first.recent_turns) == 1

    engine.run(_request("compact-turns", "question two"))
    compacted = _checkpoint_workflow(runtime, "compact-turns").conversation
    assert compacted.recent_turns == []
    assert "question one" in compacted.conversation_summary
    assert "answer:question two" in compacted.conversation_summary

    restarted, restarted_llm = _engine(runtime, history_policy=policy)
    restarted.run(_request("compact-turns", "question three"))
    assert restarted_llm.calls[0]["conversation_context"] == {
        "summary": compacted.conversation_summary,
        "turns": [],
    }
    assert llm.calls[0]["conversation_context"] is None


def test_history_compacts_when_estimated_token_boundary_is_reached() -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    policy = HistoryPolicy(max_turns=12, max_estimated_tokens=20)
    engine, _ = _engine(runtime, history_policy=policy)

    engine.run(_request("compact-tokens", "x" * 80))

    conversation = _checkpoint_workflow(runtime, "compact-tokens").conversation
    assert conversation.recent_turns == []
    assert conversation.conversation_summary
    assert len(conversation.conversation_summary) <= 4_000


def test_restart_respects_a_configured_turn_limit_above_the_default() -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    policy = HistoryPolicy(max_turns=14, max_estimated_tokens=16_000)
    engine, _ = _engine(runtime, history_policy=policy)
    for index in range(13):
        engine.run(_request("configured-limit", f"question {index}"))

    restarted, restarted_llm = _engine(runtime, history_policy=policy)
    restarted.run(_request("configured-limit", "question 13"))

    context = restarted_llm.calls[0]["conversation_context"]
    assert context is not None
    assert len(context["turns"]) == 13


def test_restart_compacts_saved_history_against_lowered_policy_before_prompt() -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    original_policy = HistoryPolicy(max_turns=4, max_estimated_tokens=16_000)
    engine, _ = _engine(runtime, history_policy=original_policy)
    for index in range(3):
        engine.run(_request("lowered-policy", f"question {index}"))

    lowered_policy = HistoryPolicy(max_turns=2, max_estimated_tokens=16_000)
    restarted, restarted_llm = _engine(runtime, history_policy=lowered_policy)
    restarted.run(_request("lowered-policy", "next question"))

    context = restarted_llm.calls[0]["conversation_context"]
    assert context is not None
    assert context["turns"] == []
    assert "question 2" in context["summary"]


def test_repeated_compaction_keeps_the_newest_completed_turn_in_summary() -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    policy = HistoryPolicy(max_turns=1, max_estimated_tokens=16_000)
    engine, _ = _engine(runtime, history_policy=policy)
    for index in range(8):
        engine.run(
            _request(
                "rolling-summary",
                f"newest-{index}-" + ("x" * 700),
            )
        )

    summary = _checkpoint_workflow(
        runtime,
        "rolling-summary",
    ).conversation.conversation_summary
    assert len(summary) <= 4_000
    assert "newest-7" in summary


def test_archiving_conversation_deletes_its_checkpoint_history(session_factory) -> None:
    with session_factory() as session:
        session.add(
            User(
                id="checkpoint-user",
                email="checkpoint@example.com",
                normalized_email="checkpoint@example.com",
                display_name="Checkpoint User",
            )
        )
        session.flush()
        session.add(
            LearningGoal(
                id="checkpoint-goal",
                user_id="checkpoint-user",
                title="Checkpoint goal",
                target_outcome="Verify cleanup",
                weekly_hours_target=4,
            )
        )
        session.commit()

    runtime = InMemoryTutorCheckpointRuntime()
    with session_factory() as session:
        thread = ConversationService(session).create_thread(
            user_id="checkpoint-user",
            goal_id="checkpoint-goal",
        )
        session.commit()

    engine, _ = _engine(runtime)
    engine.run(
        _request(
            thread.id,
            "remember temporarily",
            user_id="checkpoint-user",
            goal_id="checkpoint-goal",
        )
    )
    assert runtime.saver.get_tuple(
        {"configurable": {"thread_id": thread.id}}
    ) is not None

    with session_factory() as session:
        archived = ConversationService(
            session,
            checkpoint_runtime=runtime,
        ).archive_thread(
            user_id="checkpoint-user",
            goal_id="checkpoint-goal",
            thread_id=thread.id,
        )
        session.commit()

    assert archived.status == "archived"
    assert runtime.saver.get_tuple(
        {"configurable": {"thread_id": thread.id}}
    ) is None
