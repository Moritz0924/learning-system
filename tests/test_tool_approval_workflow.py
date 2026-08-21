from __future__ import annotations

from datetime import datetime, timezone

import pytest
from mcp import types
from sqlalchemy import func, select

from backend.app.application.mcp_service import McpApplicationService
from backend.app.application.tool_approval_service import (
    ToolApprovalApplicationService,
    ToolApprovalConflict,
    ToolApprovalNotFound,
    recover_stranded_tool_approvals,
)
from backend.app.application.conversation_service import ConversationService
from backend.app.domain.conversation import ActiveRunConflict
from backend.app.models import (
    AgentRun,
    LearningGoal,
    User,
    UserMcpServer,
    UserMcpTool,
    UserSecretReference,
    UserToolApproval,
)
from tests.fakes.secret_store import InMemorySecretStore
from tests.test_mcp_application_service import FakeSession, FakeSessionFactory


def _seed_run(session, *, user_id: str, suffix: str = ""):
    session.add(
        User(
            id=user_id,
            email=f"{user_id}@example.test",
            normalized_email=f"{user_id}@example.test",
            display_name=user_id,
        )
    )
    session.flush()
    goal_id = f"goal-{user_id}{suffix}"
    session.add(
        LearningGoal(
            id=goal_id,
            user_id=user_id,
            title="Approval test",
            target_outcome="Test durable approval",
            weekly_hours_target=4,
        )
    )
    session.commit()
    service = ConversationService(session)
    thread = service.create_thread(user_id=user_id, goal_id=goal_id, title=None)
    run = service.start_run(
        user_id=user_id,
        goal_id=goal_id,
        thread_id=thread.id,
        correlation_id=f"correlation-{user_id}{suffix}",
        request_hash=f"request-{user_id}{suffix}",
        graph_name="phase2_tutor_graph",
        graph_version="phase2-v1",
        trigger_type="chat",
        input_snapshot={
            "source": "tutor_chat_stream",
            "goal_id": goal_id,
            "thread_id": thread.id,
            "request": {
                "trigger_type": "chat",
                "user_id": user_id,
                "goal_id": goal_id,
                "thread_id": thread.id,
                "user_message": "Create the item",
                "assessment_type": "daily",
                "assessment_id": None,
                "knowledge_node_ids": [],
                "submitted_answers": {},
                "metadata": {},
                "skill_ids": None,
                "memory_candidates": [],
            },
        },
    )
    session.commit()
    return thread, run


def _seed_write_tool(session, *, user_id: str, server_id: str = "write-server"):
    server = UserMcpServer(
        id=server_id,
        user_id=user_id,
        name="Visible Writer",
        transport="streamable_http",
        url="https://mcp.example.test/connect",
        enabled=True,
    )
    session.add(server)
    session.add(
        UserMcpTool(
            id=f"tool-{server_id}",
            mcp_server_id=server.id,
            name="create_item",
            description="Create an item",
            input_schema_json={"type": "object"},
            annotations_json={},
            enabled=True,
        )
    )
    session.commit()
    return server


def test_approval_is_durable_sanitized_and_reused_by_stable_request_hash(db_session) -> None:
    thread, run = _seed_run(db_session, user_id="owner")
    server = _seed_write_tool(db_session, user_id="owner")
    store = InMemorySecretStore()
    store.put("approval-secret-ref", "approval-secret-value")
    db_session.add(
        UserSecretReference(
            id="approval-secret-row",
            user_id="owner",
            owner_type="mcp_server",
            owner_id=server.id,
            slot="header:Authorization",
            secret_ref="approval-secret-ref",
            configured=True,
            masked_value="********",
        )
    )
    db_session.commit()
    factory = FakeSessionFactory(FakeSession())
    mcp = McpApplicationService(
        db_session,
        user_id="owner",
        secret_store=store,
        session_factory=factory,
        resolver=lambda _: ["203.0.113.10"],
    )
    approvals = ToolApprovalApplicationService(
        db_session,
        user_id="owner",
        mcp_service=mcp,
    )

    first = approvals.require_approval(
        run_id=run.id,
        thread_id=thread.id,
        server_id=server.id,
        tool_name="create_item",
        arguments={"title": "approval-secret-value", "token": "also-sensitive"},
    )
    second = approvals.require_approval(
        run_id=run.id,
        thread_id=thread.id,
        server_id=server.id,
        tool_name="create_item",
        arguments={"token": "also-sensitive", "title": "approval-secret-value"},
    )

    assert first == second
    assert first["server"] == {"id": server.id, "name": "Visible Writer"}
    assert first["tool_name"] == "create_item"
    assert first["arguments"] == {"title": "[redacted]", "token": "[redacted]"}
    assert "approval-secret-value" not in repr(first)
    assert db_session.scalar(select(func.count()).select_from(UserToolApproval)) == 1
    approval = db_session.scalar(select(UserToolApproval))
    assert approval.run_id == run.id
    assert approval.status == "pending"
    assert approval.arguments_json == first["arguments"]
    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).status == "awaiting_approval"
    assert factory.connections == []


def test_approve_executes_once_replays_sanitized_result_and_duplicate_decision_conflicts(
    db_session,
) -> None:
    thread, run = _seed_run(db_session, user_id="owner")
    server = _seed_write_tool(db_session, user_id="owner")
    fake = FakeSession(
        result=types.CallToolResult(
            structuredContent={"created": True, "token": "provider-secret"},
            content=[],
        )
    )
    factory = FakeSessionFactory(fake)
    approvals = ToolApprovalApplicationService(
        db_session,
        user_id="owner",
        mcp_service=McpApplicationService(
            db_session,
            user_id="owner",
            secret_store=None,
            session_factory=factory,
            resolver=lambda _: ["203.0.113.10"],
        ),
    )
    payload = approvals.require_approval(
        run_id=run.id,
        thread_id=thread.id,
        server_id=server.id,
        tool_name="create_item",
        arguments={"title": "safe"},
    )

    decision = approvals.begin_decision(
        run_id=run.id,
        approval_id=payload["approval_id"],
        decision="approve",
    )
    first = approvals.resolve_after_interrupt(
        run_id=run.id,
        thread_id=thread.id,
        server_id=server.id,
        tool_name="create_item",
        arguments={"title": "safe"},
        decision=decision.decision,
    )
    replay = approvals.resolve_after_interrupt(
        run_id=run.id,
        thread_id=thread.id,
        server_id=server.id,
        tool_name="create_item",
        arguments={"title": "safe"},
        decision="approve",
    )

    assert first.status == "success"
    assert replay.status == "success" and replay.cache_hit is True
    assert first.value == replay.value == {"created": True, "token": "[redacted]"}
    assert fake.calls == [("create_item", {"title": "safe"})]
    assert factory.closed == 1
    approval = db_session.get(UserToolApproval, payload["approval_id"])
    assert approval.status == "completed"
    assert "provider-secret" not in repr(approval.result_summary_json)
    with pytest.raises(ToolApprovalConflict) as duplicate:
        approvals.begin_decision(
            run_id=run.id,
            approval_id=payload["approval_id"],
            decision="approve",
        )
    assert duplicate.value.payload == {
        "code": "mcp.approval_not_pending",
        "approval_id": payload["approval_id"],
        "status": "completed",
        "result_summary": approval.result_summary_json,
    }


def test_reject_never_opens_transport_and_cross_owner_or_run_tampering_is_hidden(
    db_session,
) -> None:
    thread, run = _seed_run(db_session, user_id="owner")
    _other_thread, other_run = _seed_run(db_session, user_id="other")
    server = _seed_write_tool(db_session, user_id="owner")
    factory = FakeSessionFactory(FakeSession())
    approvals = ToolApprovalApplicationService(
        db_session,
        user_id="owner",
        mcp_service=McpApplicationService(
            db_session,
            user_id="owner",
            secret_store=None,
            session_factory=factory,
        ),
    )
    payload = approvals.require_approval(
        run_id=run.id,
        thread_id=thread.id,
        server_id=server.id,
        tool_name="create_item",
        arguments={},
    )

    with pytest.raises(ToolApprovalNotFound):
        ToolApprovalApplicationService(
            db_session,
            user_id="other",
            mcp_service=approvals.mcp_service,
        ).begin_decision(
            run_id=run.id,
            approval_id=payload["approval_id"],
            decision="reject",
        )
    with pytest.raises(ToolApprovalNotFound):
        approvals.begin_decision(
            run_id=other_run.id,
            approval_id=payload["approval_id"],
            decision="reject",
        )

    decision = approvals.begin_decision(
        run_id=run.id,
        approval_id=payload["approval_id"],
        decision="reject",
    )
    result = approvals.resolve_after_interrupt(
        run_id=run.id,
        thread_id=thread.id,
        server_id=server.id,
        tool_name="create_item",
        arguments={},
        decision=decision.decision,
    )

    assert result.status == "failed"
    assert result.error_code == "mcp.tool_rejected"
    assert factory.connections == []


def test_startup_recovery_marks_executing_unknown_without_retry(db_session) -> None:
    thread, run = _seed_run(db_session, user_id="owner")
    server = _seed_write_tool(db_session, user_id="owner")
    approval = UserToolApproval(
        id="approval-executing",
        user_id="owner",
        run_id=run.id,
        mcp_server_id=server.id,
        tool_name="create_item",
        arguments_json={},
        request_hash="a" * 64,
        status="executing",
        result_summary_json={},
        created_at=datetime.now(timezone.utc),
        decided_at=datetime.now(timezone.utc),
    )
    run_model = db_session.get(AgentRun, run.id)
    run_model.status = "running"
    db_session.add(approval)
    db_session.commit()

    recovered = recover_stranded_tool_approvals(db_session)

    assert recovered == 1
    db_session.expire_all()
    assert db_session.get(UserToolApproval, approval.id).status == "unknown"
    assert db_session.get(AgentRun, run.id).status == "failed"


def test_awaiting_approval_remains_single_active_cancellable_run(db_session) -> None:
    thread, run = _seed_run(db_session, user_id="owner")
    server = _seed_write_tool(db_session, user_id="owner")
    approvals = ToolApprovalApplicationService(
        db_session,
        user_id="owner",
        mcp_service=McpApplicationService(
            db_session,
            user_id="owner",
            secret_store=None,
            session_factory=FakeSessionFactory(FakeSession()),
        ),
    )
    payload = approvals.require_approval(
        run_id=run.id,
        thread_id=thread.id,
        server_id=server.id,
        tool_name="create_item",
        arguments={},
    )

    with pytest.raises(ActiveRunConflict):
        ConversationService(db_session).start_run(
            user_id="owner",
            goal_id=thread.goal_id,
            thread_id=thread.id,
            correlation_id="approval-conflict",
            request_hash="b" * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={},
        )
    cancelled = ConversationService(db_session).request_owned_run_cancellation(
        user_id="owner", run_id=run.id
    )
    db_session.commit()
    assert cancelled.status == "cancellation_requested"
    with pytest.raises(ToolApprovalConflict) as rejected_resume:
        approvals.begin_decision(
            run_id=run.id,
            approval_id=payload["approval_id"],
            decision="approve",
        )
    assert rejected_resume.value.payload["code"] == "mcp.approval_not_resumable"
