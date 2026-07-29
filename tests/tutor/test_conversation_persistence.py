from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.application.conversation_service import ConversationService
from backend.app.application.tutor_service import answer_tutor_question
from backend.app.domain.conversation import (
    ActiveRunConflict,
    ConversationNotFound,
    ConversationThreadArchived,
    RunNotFound,
)
from backend.app.infrastructure.persistence.repositories.conversation_repository import (
    SQLAlchemyAgentRunRepository,
    SQLAlchemyConversationRepository,
)
from backend.app.models import AgentRun, ConversationThread, LearningGoal, User
from adaptive_tutor.phase2.schemas import TutorRunResult


def _seed_scope(session_factory) -> None:
    with session_factory() as session:
        session.add_all(
            [
                User(
                    id="user-a",
                    email="a@example.com",
                    normalized_email="a@example.com",
                    display_name="A",
                ),
                User(
                    id="user-b",
                    email="b@example.com",
                    normalized_email="b@example.com",
                    display_name="B",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                LearningGoal(
                    id="goal-a",
                    user_id="user-a",
                    title="A goal",
                    target_outcome="Learn A",
                    weekly_hours_target=4,
                ),
                LearningGoal(
                    id="goal-a2",
                    user_id="user-a",
                    title="A second goal",
                    target_outcome="Learn A2",
                    weekly_hours_target=4,
                ),
                LearningGoal(
                    id="goal-b",
                    user_id="user-b",
                    title="B goal",
                    target_outcome="Learn B",
                    weekly_hours_target=4,
                ),
            ]
        )
        session.commit()


def test_thread_lifecycle_uses_server_generated_ids_and_persists_archive(session_factory) -> None:
    _seed_scope(session_factory)
    with session_factory() as session:
        service = ConversationService(session)
        created = service.create_thread(user_id="user-a", goal_id="goal-a", title="RAG study")
        session.commit()

        assert created.id.startswith("thread-")
        UUID(created.id.removeprefix("thread-"))
        assert created.status == "active"
        assert created.title == "RAG study"

    with session_factory() as session:
        service = ConversationService(session)
        assert [item.id for item in service.list_threads(user_id="user-a", goal_id="goal-a")] == [
            created.id
        ]
        archived = service.archive_thread(
            user_id="user-a", goal_id="goal-a", thread_id=created.id
        )
        session.commit()
        assert archived.status == "archived"
        assert archived.archived_at is not None

    with session_factory() as session:
        persisted = session.get(ConversationThread, created.id)
        assert persisted is not None
        assert persisted.status == "archived"
        assert persisted.archived_at is not None


def test_thread_access_never_crosses_user_or_goal_scope(session_factory) -> None:
    _seed_scope(session_factory)
    with session_factory() as session:
        service = ConversationService(session)
        thread = service.create_thread(user_id="user-a", goal_id="goal-a")
        session.commit()

    with session_factory() as session:
        service = ConversationService(session)
        with pytest.raises(ConversationNotFound):
            service.get_thread(user_id="user-b", goal_id="goal-b", thread_id=thread.id)
        with pytest.raises(ConversationNotFound):
            service.get_thread(user_id="user-a", goal_id="goal-a2", thread_id=thread.id)
        with pytest.raises(ConversationNotFound):
            service.archive_thread(
                user_id="user-a", goal_id="goal-a2", thread_id=thread.id
            )
        with pytest.raises(ConversationNotFound):
            service.require_thread(user_id="user-a", goal_id="goal-a2", thread_id=thread.id)
        assert service.list_threads(user_id="user-b", goal_id="goal-b") == []


def test_goal_must_belong_to_thread_owner(session_factory) -> None:
    _seed_scope(session_factory)
    with session_factory() as session:
        with pytest.raises(ConversationNotFound):
            ConversationService(session).create_thread(user_id="user-a", goal_id="goal-b")


def test_only_one_active_run_can_exist_per_thread(session_factory) -> None:
    _seed_scope(session_factory)
    with session_factory() as session:
        thread = SQLAlchemyConversationRepository(session).create(
            user_id="user-a", goal_id="goal-a", title=None
        )
        session.commit()

    with session_factory() as first_session:
        runs = SQLAlchemyAgentRunRepository(first_session)
        first = runs.start(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            correlation_id="2d2d9115-8eb1-48c7-82f5-5b925d860ceb",
            request_hash="a" * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={"message": "first"},
        )
        first_session.commit()

    with session_factory() as second_session:
        runs = SQLAlchemyAgentRunRepository(second_session)
        with pytest.raises(ActiveRunConflict):
            runs.start(
                user_id="user-a",
                goal_id="goal-a",
                thread_id=thread.id,
                correlation_id="8ad38104-bc3b-4b51-bdb1-249d9b7530f8",
                request_hash="b" * 64,
                graph_name="phase2_tutor_graph",
                graph_version="phase2-v1",
                trigger_type="chat",
                input_snapshot={"message": "second"},
            )

    with session_factory() as session:
        SQLAlchemyAgentRunRepository(session).complete(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            run_id=first.id,
            output_snapshot={"answer": "done"},
            node_trace=[{"node": "teacher", "status": "ok"}],
            latency_ms=12,
        )
        replacement = SQLAlchemyAgentRunRepository(session).start(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            correlation_id="51d2997c-e521-4d45-94e0-01971b057a4d",
            request_hash="c" * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={"message": "replacement"},
        )
        session.commit()
        assert replacement.status == "running"


def test_archived_thread_cannot_start_a_run(session_factory) -> None:
    _seed_scope(session_factory)
    with session_factory() as session:
        repository = SQLAlchemyConversationRepository(session)
        thread = repository.create(user_id="user-a", goal_id="goal-a", title=None)
        repository.archive(user_id="user-a", goal_id="goal-a", thread_id=thread.id)
        with pytest.raises(ConversationThreadArchived):
            SQLAlchemyAgentRunRepository(session).start(
                user_id="user-a",
                goal_id="goal-a",
                thread_id=thread.id,
                correlation_id="5b11ea25-cb08-40c1-8ab9-83d7e236fb77",
                request_hash="d" * 64,
                graph_name="phase2_tutor_graph",
                graph_version="phase2-v1",
                trigger_type="chat",
                input_snapshot={},
            )


def test_cancellation_request_is_owner_scoped_and_persistent(session_factory) -> None:
    _seed_scope(session_factory)
    with session_factory() as session:
        thread = SQLAlchemyConversationRepository(session).create(
            user_id="user-a", goal_id="goal-a", title=None
        )
        run = SQLAlchemyAgentRunRepository(session).start(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            correlation_id="227788e3-36ca-425b-9c9e-a1f471b86ef5",
            request_hash="e" * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={},
        )
        session.commit()

    with session_factory() as session:
        runs = SQLAlchemyAgentRunRepository(session)
        with pytest.raises(RunNotFound):
            runs.request_cancel(
                user_id="user-b", goal_id="goal-b", thread_id=thread.id, run_id=run.id
            )
        with pytest.raises(RunNotFound):
            runs.request_cancel(
                user_id="user-a", goal_id="goal-a2", thread_id=thread.id, run_id=run.id
            )
        cancelled = runs.request_cancel(
            user_id="user-a", goal_id="goal-a", thread_id=thread.id, run_id=run.id
        )
        session.commit()
        assert cancelled.status == "cancellation_requested"
        assert cancelled.cancel_requested_at is not None

    with session_factory() as session:
        runs = SQLAlchemyAgentRunRepository(session)
        assert runs.is_cancel_requested(
            user_id="user-a", goal_id="goal-a", thread_id=thread.id, run_id=run.id
        )
        final = runs.mark_cancelled(
            user_id="user-a", goal_id="goal-a", thread_id=thread.id, run_id=run.id
        )
        session.commit()
        assert final.status == "cancelled"
        assert final.cancelled_at is not None
        assert final.completed_at is not None


def test_run_trace_and_correlation_fields_are_persisted(session_factory) -> None:
    _seed_scope(session_factory)
    with session_factory() as session:
        thread = SQLAlchemyConversationRepository(session).create(
            user_id="user-a", goal_id="goal-a", title=None
        )
        run = SQLAlchemyAgentRunRepository(session).start(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            correlation_id="3754704b-3849-4e6e-89b8-134686f12c6b",
            request_hash="f" * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={"message": "trace me"},
        )
        SQLAlchemyAgentRunRepository(session).complete(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            run_id=run.id,
            output_snapshot={"answer": "traced"},
            node_trace=[{"node": "load_context", "status": "ok"}],
            latency_ms=9,
        )
        session.commit()

    with session_factory() as session:
        persisted = session.scalar(select(AgentRun).where(AgentRun.id == run.id))
        assert persisted is not None
        assert persisted.correlation_id == "3754704b-3849-4e6e-89b8-134686f12c6b"
        assert persisted.request_hash == "f" * 64
        assert persisted.goal_id == "goal-a"
        assert persisted.node_trace == [{"node": "load_context", "status": "ok"}]
        assert persisted.status == "success"
        assert persisted.started_at is not None
        assert persisted.completed_at is not None


def test_application_service_owns_the_complete_run_lifecycle(session_factory) -> None:
    _seed_scope(session_factory)
    with session_factory() as session:
        service = ConversationService(session)
        thread = service.create_thread(user_id="user-a", goal_id="goal-a")
        run = service.start_run(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            correlation_id="8366a4a3-3bf8-4132-a32b-c53d79c79689",
            request_hash="1" * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={"message": "service"},
        )
        completed = service.complete_run(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            run_id=run.id,
            output_snapshot={"answer": "service complete"},
            node_trace=[{"node": "persist", "status": "ok"}],
            latency_ms=4,
        )
        session.commit()

        assert completed.status == "success"
        assert completed.output_snapshot == {"answer": "service complete"}


def test_cancellation_request_wins_over_late_completion_and_failure(session_factory) -> None:
    _seed_scope(session_factory)
    with session_factory() as session:
        conversations = SQLAlchemyConversationRepository(session)
        completion_thread = conversations.create(
            user_id="user-a", goal_id="goal-a", title=None
        )
        failure_thread = conversations.create(
            user_id="user-a", goal_id="goal-a", title=None
        )
        runs = SQLAlchemyAgentRunRepository(session)
        completion_run = runs.start(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=completion_thread.id,
            correlation_id="c92d1138-1ef2-4d9f-aa5b-35e30b879bec",
            request_hash="2" * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={},
        )
        failure_run = runs.start(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=failure_thread.id,
            correlation_id="46912118-7fa3-460c-a4b4-cf85f75b70b2",
            request_hash="3" * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={},
        )
        runs.request_cancel(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=completion_thread.id,
            run_id=completion_run.id,
        )
        completed = runs.complete(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=completion_thread.id,
            run_id=completion_run.id,
            output_snapshot={"answer": "too late"},
            node_trace=[{"node": "late"}],
            latency_ms=5,
        )
        runs.request_cancel(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=failure_thread.id,
            run_id=failure_run.id,
        )
        failed = runs.fail(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=failure_thread.id,
            run_id=failure_run.id,
            error_message="too late",
            node_trace=[{"node": "late"}],
            latency_ms=6,
        )

        assert completed.status == "cancelled"
        assert completed.output_snapshot == {}
        assert completed.cancelled_at is not None
        assert failed.status == "cancelled"
        assert failed.error_message is None


@pytest.mark.parametrize(
    ("run_user_id", "run_goal_id"),
    [("user-a", "goal-a2"), ("user-b", "goal-b")],
)
def test_database_rejects_agent_run_thread_ownership_mismatches(
    session_factory, run_user_id: str, run_goal_id: str
) -> None:
    _seed_scope(session_factory)
    with session_factory() as session:
        thread = SQLAlchemyConversationRepository(session).create(
            user_id="user-a", goal_id="goal-a", title=None
        )
        session.commit()

    with session_factory() as session:
        session.add(
            AgentRun(
                id=f"run-mismatch-{run_user_id}-{run_goal_id}",
                user_id=run_user_id,
                goal_id=run_goal_id,
                thread_id=thread.id,
                correlation_id=f"correlation-{run_user_id}-{run_goal_id}",
                request_hash="4" * 64,
                graph_name="phase2_tutor_graph",
                graph_version="phase2-v1",
                trigger_type="chat",
                input_snapshot={},
                output_snapshot={},
                node_trace=[],
                status="success",
                latency_ms=0,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_legacy_alias_maps_to_stable_server_threads_per_user_and_goal(session_factory) -> None:
    _seed_scope(session_factory)
    with session_factory() as session:
        repository = SQLAlchemyConversationRepository(session)
        first_goal = repository.ensure_legacy(
            user_id="user-a", goal_id="goal-a", thread_id="fixed-frontend-thread"
        )
        same_goal = repository.ensure_legacy(
            user_id="user-a", goal_id="goal-a", thread_id="fixed-frontend-thread"
        )
        second_goal = repository.ensure_legacy(
            user_id="user-a", goal_id="goal-a2", thread_id="fixed-frontend-thread"
        )

        assert first_goal.id == same_goal.id
        assert first_goal.id != second_goal.id
        assert first_goal.id != "fixed-frontend-thread"
        assert second_goal.id != "fixed-frontend-thread"
        assert first_goal.legacy_key == "fixed-frontend-thread"
        assert second_goal.legacy_key == "fixed-frontend-thread"


def test_active_run_prevents_thread_archive(session_factory) -> None:
    _seed_scope(session_factory)
    with session_factory() as session:
        conversations = SQLAlchemyConversationRepository(session)
        thread = conversations.create(user_id="user-a", goal_id="goal-a", title=None)
        SQLAlchemyAgentRunRepository(session).start(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            correlation_id="d2ef3739-eeeb-4924-be25-649d32d89dd2",
            request_hash="5" * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={},
        )
        with pytest.raises(ActiveRunConflict):
            conversations.archive(
                user_id="user-a", goal_id="goal-a", thread_id=thread.id
            )


def test_sync_tutor_reuses_legacy_alias_safely_across_two_goals(
    session_factory, monkeypatch
) -> None:
    _seed_scope(session_factory)
    captured_thread_ids: list[str] = []

    monkeypatch.setattr(
        "backend.app.application.tutor_service._prepare_tutor_context",
        lambda session, request: object(),
    )

    def fake_run_engine(session, request, *, prepared_context):
        captured_thread_ids.append(request.thread_id)
        return TutorRunResult(route="teaching", final_answer="compatible")

    monkeypatch.setattr(
        "backend.app.application.tutor_service._run_engine", fake_run_engine
    )

    with session_factory() as session:
        first = answer_tutor_question(
            session,
            user_id="user-a",
            goal_id="goal-a",
            thread_id="fixed-frontend-thread",
            message="first goal",
        )
        second = answer_tutor_question(
            session,
            user_id="user-a",
            goal_id="goal-a2",
            thread_id="fixed-frontend-thread",
            message="second goal",
        )

        assert first["final_answer"] == "compatible"
        assert second["final_answer"] == "compatible"
        assert len(set(captured_thread_ids)) == 2
        assert all(item.startswith("thread-") for item in captured_thread_ids)


@pytest.mark.parametrize("late_operation", ["complete", "fail"])
def test_committed_cancellation_wins_over_a_stale_terminal_writer(
    session_factory, late_operation: str
) -> None:
    _seed_scope(session_factory)
    with session_factory() as setup_session:
        thread = SQLAlchemyConversationRepository(setup_session).create(
            user_id="user-a", goal_id="goal-a", title=None
        )
        run = SQLAlchemyAgentRunRepository(setup_session).start(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            correlation_id=f"cancel-wins-{late_operation}",
            request_hash="6" * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={},
        )
        setup_session.commit()

    with session_factory() as stale_session, session_factory() as cancelling_session:
        stale_runs = SQLAlchemyAgentRunRepository(stale_session)
        stale_model = stale_session.get(AgentRun, run.id)
        assert stale_model.status == "running"

        SQLAlchemyAgentRunRepository(cancelling_session).request_cancel(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            run_id=run.id,
        )
        cancelling_session.commit()

        if late_operation == "complete":
            result = stale_runs.complete(
                user_id="user-a",
                goal_id="goal-a",
                thread_id=thread.id,
                run_id=run.id,
                output_snapshot={"answer": "stale"},
                node_trace=[{"node": "stale"}],
                latency_ms=10,
            )
        else:
            result = stale_runs.fail(
                user_id="user-a",
                goal_id="goal-a",
                thread_id=thread.id,
                run_id=run.id,
                error_message="stale failure",
                node_trace=[{"node": "stale"}],
                latency_ms=10,
            )
        stale_session.commit()

        assert result.status == "cancelled"
        assert stale_model.status == "cancelled"

    with session_factory() as reader:
        persisted = reader.get(AgentRun, run.id)
        assert persisted.status == "cancelled"
        assert persisted.cancel_requested_at is not None
        assert persisted.cancelled_at is not None
        assert persisted.output_snapshot == {}
        assert persisted.error_message is None


@pytest.mark.parametrize("terminal_operation", ["complete", "fail"])
def test_committed_terminal_result_rejects_a_stale_cancellation_writer(
    session_factory, terminal_operation: str
) -> None:
    _seed_scope(session_factory)
    with session_factory() as setup_session:
        thread = SQLAlchemyConversationRepository(setup_session).create(
            user_id="user-a", goal_id="goal-a", title=None
        )
        run = SQLAlchemyAgentRunRepository(setup_session).start(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            correlation_id=f"terminal-wins-{terminal_operation}",
            request_hash="7" * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={},
        )
        setup_session.commit()

    with session_factory() as stale_session, session_factory() as terminal_session:
        stale_runs = SQLAlchemyAgentRunRepository(stale_session)
        stale_model = stale_session.get(AgentRun, run.id)
        assert stale_model.status == "running"

        terminal_runs = SQLAlchemyAgentRunRepository(terminal_session)
        if terminal_operation == "complete":
            terminal_runs.complete(
                user_id="user-a",
                goal_id="goal-a",
                thread_id=thread.id,
                run_id=run.id,
                output_snapshot={"answer": "fresh"},
                node_trace=[{"node": "fresh"}],
                latency_ms=11,
            )
            expected_status = "success"
        else:
            terminal_runs.fail(
                user_id="user-a",
                goal_id="goal-a",
                thread_id=thread.id,
                run_id=run.id,
                error_message="fresh failure",
                node_trace=[{"node": "fresh"}],
                latency_ms=11,
            )
            expected_status = "failed"
        terminal_session.commit()

        result = stale_runs.request_cancel(
            user_id="user-a",
            goal_id="goal-a",
            thread_id=thread.id,
            run_id=run.id,
        )
        stale_session.commit()

        assert result.status == expected_status
        assert stale_model.status == expected_status
        assert result.cancel_requested_at is None

    with session_factory() as reader:
        persisted = reader.get(AgentRun, run.id)
        assert persisted.status == expected_status
        assert persisted.cancel_requested_at is None
        assert persisted.cancelled_at is None
