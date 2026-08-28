from __future__ import annotations

import json
from threading import Event
from types import SimpleNamespace

import pytest

from backend.app.application.conversation_service import ConversationService
from backend.app.application.tutor_stream_service import (
    TutorRunCancelled,
    begin_streaming_tutor_run,
    finish_streaming_failure,
)
from backend.app.models import AgentRun, LearningGoal, ToolCall
from backend.app.services.llm_gateway import EvaluationProviderError
from backend.app.services.llm_gateway import LLMGatewayClient
from adaptive_tutor.phase2.schemas import TutorRunResult
from tests.conftest import register_user


def _seed_goal(session_factory, *, user_id: str, goal_id: str) -> None:
    with session_factory() as session:
        session.add(
            LearningGoal(
                id=goal_id,
                user_id=user_id,
                title="Streaming tutor",
                target_outcome="Learn streaming safely",
                weekly_hours_target=4,
            )
        )
        session.commit()


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.replace("\r\n", "\n").strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            name, value = line.split(":", 1)
            fields[name] = value.lstrip()
        events.append((fields["event"], json.loads(fields["data"])))
    return events


@pytest.fixture(autouse=True)
def _test_tutor_model(monkeypatch):
    """Streaming mechanics do not depend on a user model binding."""
    monkeypatch.setattr(
        "backend.app.application.engine.RuntimeResolver.resolve_tutor_text",
        lambda _self, **_kwargs: LLMGatewayClient(),
    )


def test_tutor_chat_model_tier_is_bounded_to_flash_or_pro(client) -> None:
    identity = register_user(client, email="model-tier@example.com")
    invalid = client.post(
        "/api/tutor/chat",
        headers=identity["headers"],
        json={"goal_id": "goal-1", "thread_id": "thread-1", "message": "hello", "locale": "en-US", "model_tier": "ultra"},
    )

    assert invalid.status_code == 422


def test_conversation_http_lifecycle_is_server_managed_and_ownership_safe(
    client, session_factory
) -> None:
    owner = register_user(client, email="stream-owner@example.com")
    stranger = register_user(client, email="stream-stranger@example.com")
    _seed_goal(session_factory, user_id=owner["user_id"], goal_id="goal-stream-owner")
    _seed_goal(session_factory, user_id=stranger["user_id"], goal_id="goal-stream-stranger")

    created_response = client.post(
        "/api/tutor/conversations",
        headers=owner["headers"],
        json={"goal_id": "goal-stream-owner", "title": "RAG questions"},
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["thread_id"].startswith("thread-")
    assert created["title"] == "RAG questions"
    assert created["status"] == "active"

    listed = client.get(
        "/api/tutor/conversations?goal_id=goal-stream-owner",
        headers=owner["headers"],
    )
    assert listed.status_code == 200
    assert [item["thread_id"] for item in listed.json()["conversations"]] == [
        created["thread_id"]
    ]

    hidden = client.delete(
        f"/api/tutor/conversations/{created['thread_id']}?goal_id=goal-stream-stranger",
        headers=stranger["headers"],
    )
    assert hidden.status_code == 404

    deleted = client.delete(
        f"/api/tutor/conversations/{created['thread_id']}?goal_id=goal-stream-owner",
        headers=owner["headers"],
    )
    assert deleted.status_code == 204
    assert client.get(
        "/api/tutor/conversations?goal_id=goal-stream-owner",
        headers=owner["headers"],
    ).json() == {"conversations": []}


def test_streaming_chat_emits_only_ordered_sanitized_public_events(
    client, session_factory
) -> None:
    identity = register_user(client, email="stream-events@example.com")
    _seed_goal(session_factory, user_id=identity["user_id"], goal_id="goal-stream-events")
    conversation = client.post(
        "/api/tutor/conversations",
        headers=identity["headers"],
        json={"goal_id": "goal-stream-events", "title": None},
    ).json()
    learner_message = "Explain why streaming events need sanitization"

    response = client.post(
        "/api/tutor/chat/stream",
        headers=identity["headers"],
        json={
            "goal_id": "goal-stream-events",
            "thread_id": conversation["thread_id"],
            "message": learner_message,
            "locale": "en-US",
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert [event_type for event_type, _ in events] == [
        "run.started",
        "node.started",
        "retrieval.completed",
        "node.completed",
        "node.started",
        "teacher.delta",
        "node.completed",
        "run.completed",
    ]
    assert events[0][1]["run_id"].startswith("run-")
    assert events[0][1]["thread_id"] == conversation["thread_id"]
    assert events[1][1] == {"node": "retrieval"}
    assert events[4][1] == {"node": "teacher"}
    assert events[-1][1]["result"]["final_answer"]
    assert events[-1][1]["result"]["retrieval_backend"] == "local_json_embedding"
    forbidden_keys = {
        "prompt",
        "long_term_memory",
        "api_key",
        "traceback",
        "_sa_instance_state",
        "workflow_state",
    }

    def assert_public(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden_keys.isdisjoint(key.lower() for key in value)
            for nested in value.values():
                assert_public(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_public(nested)

    for _, data in events:
        assert_public(data)


def test_sync_chat_conflicts_with_active_stream_and_uses_managed_terminal_trace(
    client, session_factory
) -> None:
    identity = register_user(client, email="sync-managed@example.com")
    goal_id = "goal-sync-managed"
    _seed_goal(session_factory, user_id=identity["user_id"], goal_id=goal_id)
    conversation = client.post(
        "/api/tutor/conversations",
        headers=identity["headers"],
        json={"goal_id": goal_id, "title": None},
    ).json()

    with session_factory() as session:
        active_stream = begin_streaming_tutor_run(
            session,
            user_id=identity["user_id"],
            goal_id=goal_id,
            thread_id=conversation["thread_id"],
            message="hold the active slot",
        )

    conflict = client.post(
        "/api/tutor/chat",
        headers=identity["headers"],
        json={
            "goal_id": goal_id,
            "thread_id": conversation["thread_id"],
            "message": "must conflict",
            "locale": "en-US",
        },
    )
    assert conflict.status_code == 409

    with session_factory() as session:
        ConversationService(session).fail_run(
            user_id=identity["user_id"],
            goal_id=goal_id,
            thread_id=conversation["thread_id"],
            run_id=active_stream.run.id,
            error_message="test_release",
            node_trace=[],
            latency_ms=0,
        )
        session.commit()

    completed = client.post(
        "/api/tutor/chat",
        headers=identity["headers"],
        json={
            "goal_id": goal_id,
            "thread_id": conversation["thread_id"],
            "message": "complete through managed lifecycle",
            "locale": "en-US",
        },
    )
    assert completed.status_code == 200, completed.text
    with session_factory() as session:
        runs = list(
            session.query(AgentRun)
            .filter_by(
                user_id=identity["user_id"],
                goal_id=goal_id,
                thread_id=conversation["thread_id"],
            )
            .order_by(AgentRun.created_at)
        )
    assert [run.status for run in runs] == ["failed", "success"]
    assert runs[-1].node_trace
    assert runs[-1].output_snapshot["final_answer"]
    with session_factory() as session:
        tool_call = (
            session.query(ToolCall)
            .order_by(ToolCall.created_at.desc())
            .first()
        )
    assert tool_call is not None
    assert tool_call.agent_run_id == runs[-1].id


def test_cancel_run_endpoint_is_owner_only_and_durable(client, session_factory) -> None:
    owner = register_user(client, email="cancel-owner@example.com")
    stranger = register_user(client, email="cancel-stranger@example.com")
    _seed_goal(session_factory, user_id=owner["user_id"], goal_id="goal-cancel-owner")
    _seed_goal(session_factory, user_id=stranger["user_id"], goal_id="goal-cancel-stranger")

    with session_factory() as session:
        service = ConversationService(session)
        thread = service.create_thread(
            user_id=owner["user_id"], goal_id="goal-cancel-owner"
        )
        run = service.start_run(
            user_id=owner["user_id"],
            goal_id="goal-cancel-owner",
            thread_id=thread.id,
            correlation_id="2ea3c2c1-1fad-4cbb-94bd-a1d3f2e3ade8",
            request_hash="a" * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={"source": "stream"},
        )
        session.commit()

    hidden = client.post(
        f"/api/tutor/runs/{run.id}/cancel", headers=stranger["headers"]
    )
    assert hidden.status_code == 404

    cancelled = client.post(
        f"/api/tutor/runs/{run.id}/cancel", headers=owner["headers"]
    )
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json() == {
        "run_id": run.id,
        "status": "cancellation_requested",
    }

    with session_factory() as session:
        persisted = session.get(AgentRun, run.id)
        assert persisted is not None
        assert persisted.status == "cancellation_requested"
        assert persisted.cancel_requested_at is not None


def test_stream_failure_is_sanitized_and_persisted(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="stream-failure@example.com")
    _seed_goal(session_factory, user_id=identity["user_id"], goal_id="goal-stream-failure")
    conversation = client.post(
        "/api/tutor/conversations",
        headers=identity["headers"],
        json={"goal_id": "goal-stream-failure", "title": None},
    ).json()

    def fail_without_leaking(*args, **kwargs):
        raise RuntimeError("secret provider traceback and api_key=never-send")

    monkeypatch.setattr(
        "backend.app.routers.tutor.execute_streaming_tutor_run",
        fail_without_leaking,
    )
    response = client.post(
        "/api/tutor/chat/stream",
        headers=identity["headers"],
        json={
            "goal_id": "goal-stream-failure",
            "thread_id": conversation["thread_id"],
            "message": "Trigger controlled failure",
            "locale": "en-US",
        },
    )

    events = _parse_sse(response.text)
    assert events[-1][0] == "run.failed"
    assert events[-1][1]["code"] == "tutor.run_failed"
    assert "secret provider" not in response.text
    assert "api_key" not in response.text
    with session_factory() as session:
        persisted = session.get(AgentRun, events[0][1]["run_id"])
        assert persisted is not None
        assert persisted.status == "failed"
        assert persisted.error_message == "RuntimeError"


def test_stream_provider_failure_uses_stable_sanitized_runtime_code(
    client, session_factory, monkeypatch
) -> None:
    """Mapping EvaluationProviderError to generic tutor.run_failed must fail this test."""
    identity = register_user(client, email="stream-provider-failure@example.com")
    _seed_goal(
        session_factory,
        user_id=identity["user_id"],
        goal_id="goal-stream-provider-failure",
    )
    conversation = client.post(
        "/api/tutor/conversations",
        headers=identity["headers"],
        json={"goal_id": "goal-stream-provider-failure", "title": None},
    ).json()

    def fail_provider(*args, **kwargs):
        raise EvaluationProviderError(
            "provider body must not leak",
            error_code="provider_request_failed",
            request_latency_ms=1,
            total_latency_ms=1,
            retry_count=0,
        )

    monkeypatch.setattr(
        "backend.app.routers.tutor.execute_streaming_tutor_run", fail_provider
    )
    response = client.post(
        "/api/tutor/chat/stream",
        headers=identity["headers"],
        json={
            "goal_id": "goal-stream-provider-failure",
            "thread_id": conversation["thread_id"],
            "message": "Trigger provider failure",
            "locale": "en-US",
        },
    )

    events = _parse_sse(response.text)
    assert events[-1][0] == "run.failed"
    assert events[-1][1]["code"] == "runtime.provider_call_failed"
    assert "provider body" not in response.text


def test_checkpoint_finalization_failure_never_persists_managed_success(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="checkpoint-failure@example.com")
    _seed_goal(
        session_factory,
        user_id=identity["user_id"],
        goal_id="goal-checkpoint-failure",
    )
    conversation = client.post(
        "/api/tutor/conversations",
        headers=identity["headers"],
        json={"goal_id": "goal-checkpoint-failure", "title": None},
    ).json()

    def fail_checkpoint(*args, **kwargs):
        raise RuntimeError("checkpoint finalization failed")

    monkeypatch.setattr(
        "backend.app.application.engine.Phase2TutorEngine.finalize_chat_history",
        fail_checkpoint,
    )
    response = client.post(
        "/api/tutor/chat/stream",
        headers=identity["headers"],
        json={
            "goal_id": "goal-checkpoint-failure",
            "thread_id": conversation["thread_id"],
            "message": "Do not persist success before history",
            "locale": "en-US",
        },
    )

    events = _parse_sse(response.text)
    assert events[-1][0] == "run.failed"
    assert all(event_type != "run.completed" for event_type, _ in events)
    with session_factory() as session:
        persisted = session.get(AgentRun, events[0][1]["run_id"])
        assert persisted is not None
        assert persisted.status == "failed"
        assert persisted.output_snapshot == {}
        assert persisted.error_message == "RuntimeError"


def test_cancelled_stream_emits_terminal_cancel_event(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="stream-cancelled@example.com")
    _seed_goal(session_factory, user_id=identity["user_id"], goal_id="goal-stream-cancelled")
    conversation = client.post(
        "/api/tutor/conversations",
        headers=identity["headers"],
        json={"goal_id": "goal-stream-cancelled", "title": None},
    ).json()

    def cancel_run(*args, **kwargs):
        kwargs["disconnected"].set()
        raise TutorRunCancelled

    monkeypatch.setattr(
        "backend.app.routers.tutor.execute_streaming_tutor_run",
        cancel_run,
    )
    response = client.post(
        "/api/tutor/chat/stream",
        headers=identity["headers"],
        json={
            "goal_id": "goal-stream-cancelled",
            "thread_id": conversation["thread_id"],
            "message": "Cancel this run",
            "locale": "en-US",
        },
    )

    events = _parse_sse(response.text)
    assert events[-1] == (
        "run.cancelled",
        {"run_id": events[0][1]["run_id"]},
    )
    with session_factory() as session:
        persisted = session.get(AgentRun, events[0][1]["run_id"])
        assert persisted is not None
        assert persisted.status == "cancelled"
        assert persisted.cancelled_at is not None


def test_disconnect_signal_is_converted_to_durable_cancellation(session_factory) -> None:
    from backend.app.models import User

    with session_factory() as session:
        session.add(
            User(
                id="disconnect-user",
                email="disconnect@example.com",
                normalized_email="disconnect@example.com",
                display_name="Disconnect",
            )
        )
        session.commit()
    _seed_goal(
        session_factory,
        user_id="disconnect-user",
        goal_id="goal-disconnect",
    )
    with session_factory() as session:
        thread = ConversationService(session).create_thread(
            user_id="disconnect-user", goal_id="goal-disconnect"
        )
        session.commit()
        streaming_run = begin_streaming_tutor_run(
            session,
            user_id="disconnect-user",
            goal_id="goal-disconnect",
            thread_id=thread.id,
            message="Disconnect before completion",
        )
        disconnected = Event()
        disconnected.set()

        status_name = finish_streaming_failure(
            session,
            streaming_run,
            error=RuntimeError("transport closed"),
            disconnected=disconnected,
        )

    assert status_name == "cancelled"
    with session_factory() as session:
        persisted = session.get(AgentRun, streaming_run.run.id)
        assert persisted is not None
        assert persisted.status == "cancelled"


def test_streaming_chat_forwards_teacher_fragments_in_order_and_persists_once(
    client, session_factory, monkeypatch
) -> None:
    """Collapsing streamed fragments or completing the managed run twice must fail this test."""
    identity = register_user(client, email="stream-fragments@example.com")
    goal_id = "goal-stream-fragments"
    _seed_goal(session_factory, user_id=identity["user_id"], goal_id=goal_id)
    conversation = client.post(
        "/api/tutor/conversations",
        headers=identity["headers"],
        json={"goal_id": goal_id, "title": None},
    ).json()
    completions: list[str] = []

    def fake_execute(
        session,
        streaming_run,
        *,
        prepared_context,
        disconnected,
        on_teacher_delta,
    ):
        on_teacher_delta("Safe ")
        on_teacher_delta("answer")
        completions.append(streaming_run.run.id)
        ConversationService(session).complete_run(
            user_id=streaming_run.request.user_id,
            goal_id=streaming_run.request.goal_id,
            thread_id=streaming_run.request.thread_id,
            run_id=streaming_run.run.id,
            input_snapshot=streaming_run.run.input_snapshot,
            output_snapshot={"final_answer": "Safe answer"},
            node_trace=[],
            latency_ms=0,
        )
        session.commit()
        return TutorRunResult(route="teaching", final_answer="Safe answer")

    monkeypatch.setattr("backend.app.routers.tutor.execute_streaming_tutor_run", fake_execute)
    monkeypatch.setattr(
        "backend.app.routers.tutor.public_stream_result",
        lambda result: {"final_answer": result.final_answer, "citations": [], "runtime_metadata": {}},
    )

    response = client.post(
        "/api/tutor/chat/stream",
        headers=identity["headers"],
        json={
            "goal_id": goal_id,
            "thread_id": conversation["thread_id"],
            "message": "Send a safe answer in fragments",
            "locale": "en-US",
        },
    )

    events = _parse_sse(response.text)
    assert [event_type for event_type, _ in events] == [
        "run.started",
        "node.started",
        "retrieval.completed",
        "node.completed",
        "node.started",
        "teacher.delta",
        "teacher.delta",
        "node.completed",
        "run.completed",
    ]
    assert [data["delta"] for event_type, data in events if event_type == "teacher.delta"] == [
        "Safe ",
        "answer",
    ]
    assert completions == [events[0][1]["run_id"]]
    with session_factory() as session:
        persisted = session.get(AgentRun, events[0][1]["run_id"])
        assert persisted is not None
        assert persisted.status == "success"


def test_approval_resume_emits_tool_completion_before_resumed_teacher_deltas(
    client, monkeypatch
) -> None:
    """Publishing resumed teacher text before tool completion must fail this test."""
    identity = register_user(client, email="approval-stream-order@example.com")

    monkeypatch.setattr(
        "backend.app.routers.tutor.prepare_tool_approval_resume", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "backend.app.routers.tutor.begin_tool_approval_resume",
        lambda *args, **kwargs: (object(), SimpleNamespace(decision="approve")),
    )

    def fake_resume(*args, on_teacher_delta, **kwargs):
        on_teacher_delta("Resumed ")
        on_teacher_delta("answer")
        return SimpleNamespace(final_answer="Resumed answer")

    monkeypatch.setattr("backend.app.routers.tutor.execute_streaming_tutor_resume", fake_resume)
    monkeypatch.setattr(
        "backend.app.routers.tutor.public_stream_result",
        lambda result: {"final_answer": result.final_answer, "citations": [], "runtime_metadata": {}},
    )

    response = client.post(
        "/api/tutor/runs/run-order/tool-approvals/approval-order/decision",
        headers=identity["headers"],
        json={"decision": "approve"},
    )

    assert [event_type for event_type, _ in _parse_sse(response.text)] == [
        "tool.started",
        "tool.completed",
        "teacher.delta",
        "teacher.delta",
        "run.completed",
    ]


def test_approval_resume_preparation_race_emits_sanitized_failure(
    client, monkeypatch
) -> None:
    identity = register_user(client, email="approval-resume-race@example.com")
    prepared = object()
    finished: list[object] = []

    monkeypatch.setattr(
        "backend.app.routers.tutor.prepare_tool_approval_resume",
        lambda *args, **kwargs: prepared,
    )

    def fail_second_preparation(*args, **kwargs):
        raise ValueError("selected skill disappeared with private details")

    monkeypatch.setattr(
        "backend.app.routers.tutor.begin_tool_approval_resume",
        fail_second_preparation,
    )

    def finish_failure(_session, streaming_run, **_kwargs):
        finished.append(streaming_run)
        return "failed"

    monkeypatch.setattr(
        "backend.app.routers.tutor.finish_streaming_failure",
        finish_failure,
    )

    response = client.post(
        "/api/tutor/runs/run-race/tool-approvals/approval-race/decision",
        headers=identity["headers"],
        json={"decision": "approve"},
    )

    assert response.status_code == 200
    assert _parse_sse(response.text) == [
        (
            "run.failed",
            {
                "run_id": "run-race",
                "code": "mcp.resume_failed",
                "message": "The tool approval could not be resumed.",
            },
        )
    ]
    assert finished == [prepared]
