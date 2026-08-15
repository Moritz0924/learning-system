"""Durable, ownership-scoped approval state for effectful MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.application.mcp_service import McpApplicationService, McpInvocationResult, McpServiceError
from backend.app.models import AgentRun, ConversationThread, UserMcpServer, UserSecretReference, UserToolApproval


_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|auth(?:orization)?|credential|cookie|password|secret|token|(?:^|[_-])key(?:$|[_-]))",
    re.IGNORECASE,
)


class ToolApprovalNotFound(LookupError):
    pass


class ToolApprovalConflict(RuntimeError):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload["code"])
        self.payload = payload


@dataclass(frozen=True)
class ToolApprovalDecision:
    approval_id: str
    decision: Literal["approve", "reject"]


@dataclass(frozen=True)
class ToolApprovalResolution:
    status: Literal["success", "failed"]
    value: Any | None = None
    truncated: bool = False
    error_code: str | None = None
    cache_hit: bool = False


def recover_stranded_tool_approvals(session: Session) -> int:
    """Never retry a possibly side-effecting call after a process crash."""
    now = datetime.now(timezone.utc)
    approvals = session.scalars(
        select(UserToolApproval).where(UserToolApproval.status == "executing")
    ).all()
    for approval in approvals:
        approval.status = "unknown"
        approval.completed_at = now
        approval.result_summary_json = {"status": "unknown", "code": "mcp.execution_unknown"}
        run = session.get(AgentRun, approval.run_id)
        if run is not None and run.status in {"running", "awaiting_approval"}:
            run.status = "failed"
            run.error_message = "mcp.execution_unknown"
            run.completed_at = now
    if approvals:
        session.commit()
    return len(approvals)


class ToolApprovalApplicationService:
    def __init__(
        self,
        session: Session,
        *,
        user_id: str,
        mcp_service: McpApplicationService,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.mcp_service = mcp_service

    def require_approval(
        self,
        *,
        run_id: str,
        thread_id: str,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        run, server = self._owned_run_and_server(run_id=run_id, thread_id=thread_id, server_id=server_id)
        request_hash = _request_hash(run_id, server_id, tool_name, arguments)
        approval = self.session.scalar(
            select(UserToolApproval).where(
                UserToolApproval.user_id == self.user_id,
                UserToolApproval.request_hash == request_hash,
            )
        )
        if approval is None:
            approval = UserToolApproval(
                id=f"approval-{uuid4()}",
                user_id=self.user_id,
                run_id=run.id,
                mcp_server_id=server.id,
                tool_name=tool_name,
                arguments_json=_sanitize(arguments, self._configured_secret_values(server.id)),
                request_hash=request_hash,
                status="pending",
                result_summary_json={},
            )
            self.session.add(approval)
        if run.status == "running":
            run.status = "awaiting_approval"
        self.session.commit()
        return self._public(approval, server)

    def list_for_thread(self, *, thread_id: str) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(UserToolApproval, UserMcpServer)
            .join(UserMcpServer, UserMcpServer.id == UserToolApproval.mcp_server_id)
            .join(AgentRun, AgentRun.id == UserToolApproval.run_id)
            .where(
                UserToolApproval.user_id == self.user_id,
                AgentRun.thread_id == thread_id,
            )
            .order_by(UserToolApproval.created_at.asc(), UserToolApproval.id.asc())
        ).all()
        return [self._public(approval, server) for approval, server in rows]

    def begin_decision(
        self,
        *,
        run_id: str,
        approval_id: str,
        decision: Literal["approve", "reject"],
    ) -> ToolApprovalDecision:
        approval, run = self._owned_approval(run_id=run_id, approval_id=approval_id)
        thread = self.session.get(ConversationThread, run.thread_id)
        if thread is None or thread.user_id != self.user_id or thread.status != "active":
            raise ToolApprovalConflict({"code": "mcp.approval_not_resumable", "approval_id": approval.id})
        if run.status == "cancellation_requested" or run.cancel_requested_at is not None:
            raise ToolApprovalConflict({"code": "mcp.approval_not_resumable", "approval_id": approval.id})
        now = datetime.now(timezone.utc)
        values: dict[str, Any] = {
            "status": "executing" if decision == "approve" else "rejected",
            "decided_at": now,
        }
        if decision == "reject":
            values.update(
                completed_at=now,
                result_summary_json={"status": "rejected", "code": "mcp.tool_rejected"},
            )
        accepted = self.session.execute(
            update(UserToolApproval)
            .where(
                UserToolApproval.id == approval.id,
                UserToolApproval.user_id == self.user_id,
                UserToolApproval.run_id == run_id,
                UserToolApproval.status == "pending",
            )
            .values(**values),
            execution_options={"synchronize_session": False},
        )
        if accepted.rowcount != 1:
            self.session.rollback()
            current, _ = self._owned_approval(run_id=run_id, approval_id=approval_id)
            raise ToolApprovalConflict(self._conflict_payload(current))
        self.session.execute(
            update(AgentRun)
            .where(AgentRun.id == run.id, AgentRun.status == "awaiting_approval")
            .values(status="running"),
            execution_options={"synchronize_session": False},
        )
        self.session.commit()
        return ToolApprovalDecision(approval_id=approval.id, decision=decision)

    def preview_decision(
        self,
        *,
        run_id: str,
        approval_id: str,
    ) -> None:
        approval, run = self._owned_approval(run_id=run_id, approval_id=approval_id)
        thread = self.session.get(ConversationThread, run.thread_id)
        if thread is None or thread.user_id != self.user_id or thread.status != "active":
            raise ToolApprovalConflict({"code": "mcp.approval_not_resumable", "approval_id": approval.id})
        if run.status == "cancellation_requested" or run.cancel_requested_at is not None:
            raise ToolApprovalConflict({"code": "mcp.approval_not_resumable", "approval_id": approval.id})
        if approval.status != "pending":
            raise ToolApprovalConflict(self._conflict_payload(approval))

    def resolve_after_interrupt(
        self,
        *,
        run_id: str,
        thread_id: str,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        decision: Literal["approve", "reject"],
    ) -> ToolApprovalResolution:
        approval = self._approval_for_request(
            run_id=run_id,
            thread_id=thread_id,
            server_id=server_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        if approval.status == "completed":
            return _resolution_from_summary(approval.result_summary_json, cache_hit=True)
        if approval.status == "failed":
            return _resolution_from_summary(approval.result_summary_json, cache_hit=True)
        if decision == "reject" or approval.status == "rejected":
            return ToolApprovalResolution(status="failed", error_code="mcp.tool_rejected")
        if approval.status != "executing":
            raise ToolApprovalConflict(self._conflict_payload(approval))
        try:
            result = self.mcp_service.invoke_approved_tool(server_id, tool_name, arguments)
        except McpServiceError as exc:
            approval.status = "failed"
            approval.completed_at = datetime.now(timezone.utc)
            approval.result_summary_json = {"status": "failed", "code": exc.code}
            self.session.commit()
            return ToolApprovalResolution(status="failed", error_code=exc.code)
        approval.status = "completed"
        approval.completed_at = datetime.now(timezone.utc)
        approval.result_summary_json = {
            "status": "success",
            "value": _sanitize(result.value, self._configured_secret_values(server_id)),
            "truncated": result.truncated,
        }
        self.session.commit()
        return _resolution_from_summary(approval.result_summary_json)

    def _owned_run_and_server(self, *, run_id: str, thread_id: str, server_id: str) -> tuple[AgentRun, UserMcpServer]:
        run = self.session.scalar(
            select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.user_id == self.user_id,
                AgentRun.thread_id == thread_id,
            )
        )
        server = self.session.scalar(
            select(UserMcpServer).where(UserMcpServer.id == server_id, UserMcpServer.user_id == self.user_id)
        )
        if run is None or server is None:
            raise ToolApprovalNotFound("tool approval was not found")
        return run, server

    def _owned_approval(self, *, run_id: str, approval_id: str) -> tuple[UserToolApproval, AgentRun]:
        row = self.session.execute(
            select(UserToolApproval, AgentRun)
            .join(AgentRun, AgentRun.id == UserToolApproval.run_id)
            .where(
                UserToolApproval.id == approval_id,
                UserToolApproval.user_id == self.user_id,
                UserToolApproval.run_id == run_id,
                AgentRun.user_id == self.user_id,
            )
        ).one_or_none()
        if row is None:
            raise ToolApprovalNotFound("tool approval was not found")
        return row

    def _approval_for_request(
        self,
        *,
        run_id: str,
        thread_id: str,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> UserToolApproval:
        request_hash = _request_hash(run_id, server_id, tool_name, arguments)
        row = self.session.scalar(
            select(UserToolApproval)
            .join(AgentRun, AgentRun.id == UserToolApproval.run_id)
            .where(
                UserToolApproval.user_id == self.user_id,
                UserToolApproval.request_hash == request_hash,
                AgentRun.thread_id == thread_id,
            )
        )
        if row is None:
            raise ToolApprovalNotFound("tool approval was not found")
        return row

    def _configured_secret_values(self, server_id: str) -> tuple[str, ...]:
        store = self.mcp_service.secret_store
        if store is None:
            return ()
        refs = self.session.scalars(
            select(UserSecretReference).where(
                UserSecretReference.user_id == self.user_id,
                UserSecretReference.owner_type == "mcp_server",
                UserSecretReference.owner_id == server_id,
                UserSecretReference.configured.is_(True),
            )
        ).all()
        values: list[str] = []
        for ref in refs:
            try:
                value = store.get(ref.secret_ref)
            except Exception:
                continue
            if value:
                values.append(value)
        return tuple(values)

    @staticmethod
    def _public(approval: UserToolApproval, server: UserMcpServer) -> dict[str, Any]:
        return {
            "approval_id": approval.id,
            "run_id": approval.run_id,
            "server": {"id": server.id, "name": server.name},
            "tool_name": approval.tool_name,
            "arguments": approval.arguments_json,
            "status": approval.status,
            "result_summary": approval.result_summary_json,
        }

    @staticmethod
    def _conflict_payload(approval: UserToolApproval) -> dict[str, Any]:
        return {
            "code": "mcp.approval_not_pending",
            "approval_id": approval.id,
            "status": approval.status,
            "result_summary": approval.result_summary_json,
        }


def _request_hash(run_id: str, server_id: str, tool_name: str, arguments: dict[str, Any]) -> str:
    payload = {"run_id": run_id, "server_id": server_id, "tool_name": tool_name, "arguments": arguments}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _sanitize(value: Any, secret_values: tuple[str, ...] = ()) -> Any:
    if isinstance(value, str):
        sanitized = value
        for secret in secret_values:
            sanitized = sanitized.replace(secret, "[redacted]")
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item, secret_values) for item in value]
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SENSITIVE_KEY.search(str(key)) else _sanitize(item, secret_values)
            for key, item in value.items()
        }
    return value if value is None or isinstance(value, (bool, int, float)) else str(value)


def _resolution_from_summary(summary: dict[str, Any], *, cache_hit: bool = False) -> ToolApprovalResolution:
    if summary.get("status") == "success":
        return ToolApprovalResolution(
            status="success",
            value=summary.get("value"),
            truncated=bool(summary.get("truncated", False)),
            cache_hit=cache_hit,
        )
    return ToolApprovalResolution(
        status="failed",
        error_code=str(summary.get("code") or "mcp.execution_failed"),
        cache_hit=cache_hit,
    )


__all__ = [
    "ToolApprovalApplicationService",
    "ToolApprovalConflict",
    "ToolApprovalDecision",
    "ToolApprovalNotFound",
    "ToolApprovalResolution",
    "recover_stranded_tool_approvals",
]
