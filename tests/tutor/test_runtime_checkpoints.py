from __future__ import annotations

from typing import Any

import pytest

from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.schemas import TutorContext, TutorMemoryContext, TutorRunRequest
from backend.app.application.conversation_service import (
    ConversationService,
    reconcile_archived_checkpoint_threads,
)
from backend.app.infrastructure.checkpoints import (
    CheckpointConfigurationError,
    CheckpointSettings,
    HistoryPolicy,
    InMemoryTutorCheckpointRuntime,
    PostgresTutorCheckpointRuntime,
    build_checkpoint_runtime,
    initialize_checkpoint_runtime,
    shutdown_checkpoint_runtime,
)
from backend.app.models import ConversationThread, LearningGoal, User


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


def _seed_checkpoint_thread(session_factory, *, key: str):
    user_id = f"{key}-user"
    goal_id = f"{key}-goal"
    with session_factory() as session:
        session.add(
            User(
                id=user_id,
                email=f"{key}@example.com",
                normalized_email=f"{key}@example.com",
                display_name=key,
            )
        )
        session.flush()
        session.add(
            LearningGoal(
                id=goal_id,
                user_id=user_id,
                title=f"{key} goal",
                target_outcome="Verify checkpoint lifecycle",
                weekly_hours_target=4,
            )
        )
        session.commit()
    with session_factory() as session:
        thread = ConversationService(session).create_thread(
            user_id=user_id,
            goal_id=goal_id,
        )
        session.commit()
    runtime = InMemoryTutorCheckpointRuntime()
    engine, _ = _engine(runtime)
    engine.run(
        _request(
            thread.id,
            f"{key} history",
            user_id=user_id,
            goal_id=goal_id,
        )
    )
    return runtime, thread, user_id, goal_id


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


def test_sqlite_startup_selects_memory_without_hidden_test_environment(
    monkeypatch,
) -> None:
    for name in (
        "APP_ENV",
        "ENVIRONMENT",
        "TUTOR_CHECKPOINT_BACKEND",
        "TUTOR_CHECKPOINT_DATABASE_URL",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    shutdown_checkpoint_runtime()

    try:
        runtime = initialize_checkpoint_runtime()
        assert isinstance(runtime, InMemoryTutorCheckpointRuntime)
    finally:
        shutdown_checkpoint_runtime()


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
                "DATABASE_URL": "postgresql://app:secret@postgres:5432/adaptive_tutor",
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


def test_history_compacts_older_turns_and_keeps_newest_turn_verbatim() -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    policy = HistoryPolicy(max_turns=2, max_estimated_tokens=16_000)
    engine, llm = _engine(runtime, history_policy=policy)

    engine.run(_request("compact-turns", "question one"))
    first = _checkpoint_workflow(runtime, "compact-turns").conversation
    assert first.conversation_summary == ""
    assert len(first.recent_turns) == 1

    engine.run(_request("compact-turns", "question two"))
    compacted = _checkpoint_workflow(runtime, "compact-turns").conversation
    assert [item.model_dump() for item in compacted.recent_turns] == [
        {
            "user_message": "question two",
            "assistant_message": "answer:question two",
        }
    ]
    assert "question one" in compacted.conversation_summary
    assert "question two" not in compacted.conversation_summary

    restarted, restarted_llm = _engine(runtime, history_policy=policy)
    restarted.run(_request("compact-turns", "question three"))
    assert restarted_llm.calls[0]["conversation_context"] == {
        "summary": compacted.conversation_summary,
        "turns": [
            {
                "user_message": "question two",
                "assistant_message": "answer:question two",
            }
        ],
    }
    assert llm.calls[0]["conversation_context"] is None


def test_history_compacts_when_estimated_token_boundary_is_reached() -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    policy = HistoryPolicy(max_turns=12, max_estimated_tokens=20)
    engine, _ = _engine(runtime, history_policy=policy)

    engine.run(_request("compact-tokens", "older-" + ("x" * 80)))
    engine.run(_request("compact-tokens", "newest-token-turn"))

    conversation = _checkpoint_workflow(runtime, "compact-tokens").conversation
    assert [item.user_message for item in conversation.recent_turns] == [
        "newest-token-turn"
    ]
    assert "older-" in conversation.conversation_summary
    assert "newest-token-turn" not in conversation.conversation_summary
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
    assert context["turns"] == [
        {
            "user_message": "question 2",
            "assistant_message": "answer:question 2",
        }
    ]
    assert "question 1" in context["summary"]
    assert "question 2" not in context["summary"]


def test_repeated_compaction_keeps_full_newest_turn_in_recent_history() -> None:
    runtime = InMemoryTutorCheckpointRuntime()
    policy = HistoryPolicy(max_turns=1, max_estimated_tokens=16_000)
    engine, _ = _engine(runtime, history_policy=policy)
    messages: list[str] = []
    for index in range(8):
        prefix = f"newest-{index}-start-"
        suffix = f"-newest-{index}-tail"
        message = prefix + ("x" * (8_192 - len(prefix) - len(suffix))) + suffix
        messages.append(message)
        engine.run(
            _request(
                "rolling-summary",
                message,
            )
        )

    conversation = _checkpoint_workflow(
        runtime,
        "rolling-summary",
    ).conversation
    assert len(conversation.conversation_summary) <= 4_000
    assert len(conversation.recent_turns) == 1
    newest = conversation.recent_turns[0]
    assert newest.user_message == messages[-1]
    assert newest.user_message.endswith("-newest-7-tail")
    assert newest.assistant_message == f"answer:{messages[-1]}"
    assert newest.assistant_message.endswith("-newest-7-tail")


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


def test_archive_commit_failure_preserves_active_thread_checkpoint(
    session_factory,
    monkeypatch,
) -> None:
    with session_factory() as session:
        session.add(
            User(
                id="archive-failure-user",
                email="archive-failure@example.com",
                normalized_email="archive-failure@example.com",
                display_name="Archive Failure User",
            )
        )
        session.flush()
        session.add(
            LearningGoal(
                id="archive-failure-goal",
                user_id="archive-failure-user",
                title="Archive failure goal",
                target_outcome="Keep recoverable history",
                weekly_hours_target=4,
            )
        )
        session.commit()

    runtime = InMemoryTutorCheckpointRuntime()
    with session_factory() as session:
        thread = ConversationService(session).create_thread(
            user_id="archive-failure-user",
            goal_id="archive-failure-goal",
        )
        session.commit()

    engine, _ = _engine(runtime)
    engine.run(
        _request(
            thread.id,
            "recoverable history",
            user_id="archive-failure-user",
            goal_id="archive-failure-goal",
        )
    )

    with session_factory() as session:
        ConversationService(
            session,
            checkpoint_runtime=runtime,
        ).archive_thread(
            user_id="archive-failure-user",
            goal_id="archive-failure-goal",
            thread_id=thread.id,
        )
        monkeypatch.setattr(
            session,
            "commit",
            lambda: (_ for _ in ()).throw(RuntimeError("commit failed")),
        )
        with pytest.raises(RuntimeError, match="commit failed"):
            session.commit()
        session.rollback()

    assert runtime.saver.get_tuple(
        {"configurable": {"thread_id": thread.id}}
    ) is not None
    with session_factory() as session:
        persisted = session.get(ConversationThread, thread.id)
        assert persisted is not None
        assert persisted.status == "active"

    restarted, restarted_llm = _engine(runtime)
    restarted.run(
        _request(
            thread.id,
            "after rollback",
            user_id="archive-failure-user",
            goal_id="archive-failure-goal",
        )
    )
    assert restarted_llm.calls[0]["conversation_context"]["turns"][0][
        "user_message"
    ] == "recoverable history"


def test_nested_archive_commit_waits_for_root_transaction_commit(
    session_factory,
) -> None:
    with session_factory() as session:
        session.add(
            User(
                id="archive-nested-user",
                email="archive-nested@example.com",
                normalized_email="archive-nested@example.com",
                display_name="Archive Nested User",
            )
        )
        session.flush()
        session.add(
            LearningGoal(
                id="archive-nested-goal",
                user_id="archive-nested-user",
                title="Archive nested goal",
                target_outcome="Keep cleanup after root commit",
                weekly_hours_target=4,
            )
        )
        session.commit()

    runtime = InMemoryTutorCheckpointRuntime()
    with session_factory() as session:
        thread = ConversationService(session).create_thread(
            user_id="archive-nested-user",
            goal_id="archive-nested-goal",
        )
        session.commit()
    engine, _ = _engine(runtime)
    engine.run(
        _request(
            thread.id,
            "nested transaction history",
            user_id="archive-nested-user",
            goal_id="archive-nested-goal",
        )
    )

    with session_factory() as session:
        outer = session.begin()
        nested = session.begin_nested()
        ConversationService(
            session,
            checkpoint_runtime=runtime,
        ).archive_thread(
            user_id="archive-nested-user",
            goal_id="archive-nested-goal",
            thread_id=thread.id,
        )
        nested.commit()
        assert runtime.saver.get_tuple(
            {"configurable": {"thread_id": thread.id}}
        ) is not None
        outer.rollback()

    assert runtime.saver.get_tuple(
        {"configurable": {"thread_id": thread.id}}
    ) is not None
    with session_factory() as session:
        persisted = session.get(ConversationThread, thread.id)
        assert persisted is not None
        assert persisted.status == "active"


def test_closing_rolled_back_session_discards_cleanup_before_session_reuse(
    session_factory,
) -> None:
    runtime, thread, user_id, goal_id = _seed_checkpoint_thread(
        session_factory,
        key="archive-close",
    )
    session = session_factory()
    try:
        ConversationService(
            session,
            checkpoint_runtime=runtime,
        ).archive_thread(
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread.id,
        )
        session.close()

        assert runtime.saver.get_tuple(
            {"configurable": {"thread_id": thread.id}}
        ) is not None

        session.get(ConversationThread, thread.id)
        session.commit()
    finally:
        session.close()

    assert runtime.saver.get_tuple(
        {"configurable": {"thread_id": thread.id}}
    ) is not None
    with session_factory() as reader:
        persisted = reader.get(ConversationThread, thread.id)
        assert persisted is not None
        assert persisted.status == "active"


def test_unrelated_nested_rollback_keeps_root_archive_cleanup_intent(
    session_factory,
) -> None:
    runtime, thread, user_id, goal_id = _seed_checkpoint_thread(
        session_factory,
        key="archive-root-intent",
    )
    with session_factory() as session:
        ConversationService(
            session,
            checkpoint_runtime=runtime,
        ).archive_thread(
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread.id,
        )
        nested = session.begin_nested()
        nested.rollback()
        session.commit()

    assert runtime.saver.get_tuple(
        {"configurable": {"thread_id": thread.id}}
    ) is None
    with session_factory() as reader:
        persisted = reader.get(ConversationThread, thread.id)
        assert persisted is not None
        assert persisted.status == "archived"


def test_committed_archive_retries_transient_checkpoint_cleanup_failure(
    session_factory,
) -> None:
    class FlakyRuntime(InMemoryTutorCheckpointRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.failures_remaining = 1

        def delete_thread(self, thread_id: str) -> None:
            if self.failures_remaining:
                self.failures_remaining -= 1
                raise RuntimeError("checkpoint unavailable")
            super().delete_thread(thread_id)

    with session_factory() as session:
        session.add(
            User(
                id="archive-retry-user",
                email="archive-retry@example.com",
                normalized_email="archive-retry@example.com",
                display_name="Archive Retry User",
            )
        )
        session.flush()
        session.add(
            LearningGoal(
                id="archive-retry-goal",
                user_id="archive-retry-user",
                title="Archive retry goal",
                target_outcome="Retry checkpoint cleanup",
                weekly_hours_target=4,
            )
        )
        session.commit()

    runtime = FlakyRuntime()
    with session_factory() as session:
        thread = ConversationService(session).create_thread(
            user_id="archive-retry-user",
            goal_id="archive-retry-goal",
        )
        session.commit()
    engine, _ = _engine(runtime)
    engine.run(
        _request(
            thread.id,
            "delete after commit",
            user_id="archive-retry-user",
            goal_id="archive-retry-goal",
        )
    )

    with session_factory() as session:
        ConversationService(
            session,
            checkpoint_runtime=runtime,
        ).archive_thread(
            user_id="archive-retry-user",
            goal_id="archive-retry-goal",
            thread_id=thread.id,
        )
        session.commit()

    assert runtime.saver.get_tuple(
        {"configurable": {"thread_id": thread.id}}
    ) is not None
    runtime.retry_pending_deletions()
    assert runtime.saver.get_tuple(
        {"configurable": {"thread_id": thread.id}}
    ) is None


def test_reconciliation_removes_checkpoint_for_already_archived_thread(
    session_factory,
) -> None:
    with session_factory() as session:
        session.add(
            User(
                id="archive-reconcile-user",
                email="archive-reconcile@example.com",
                normalized_email="archive-reconcile@example.com",
                display_name="Archive Reconcile User",
            )
        )
        session.flush()
        session.add(
            LearningGoal(
                id="archive-reconcile-goal",
                user_id="archive-reconcile-user",
                title="Archive reconcile goal",
                target_outcome="Reconcile checkpoint cleanup",
                weekly_hours_target=4,
            )
        )
        session.commit()

    runtime = InMemoryTutorCheckpointRuntime()
    with session_factory() as session:
        thread = ConversationService(session).create_thread(
            user_id="archive-reconcile-user",
            goal_id="archive-reconcile-goal",
        )
        session.commit()
    engine, _ = _engine(runtime)
    engine.run(
        _request(
            thread.id,
            "orphaned archive checkpoint",
            user_id="archive-reconcile-user",
            goal_id="archive-reconcile-goal",
        )
    )

    with session_factory() as session:
        ConversationService(session).archive_thread(
            user_id="archive-reconcile-user",
            goal_id="archive-reconcile-goal",
            thread_id=thread.id,
        )
        session.commit()
    assert runtime.saver.get_tuple(
        {"configurable": {"thread_id": thread.id}}
    ) is not None

    with session_factory() as session:
        reconciled = reconcile_archived_checkpoint_threads(session, runtime)

    assert reconciled == 1
    assert runtime.saver.get_tuple(
        {"configurable": {"thread_id": thread.id}}
    ) is None
