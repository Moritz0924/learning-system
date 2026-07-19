from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.app.models import AgentRun, ToolCall


@dataclass
class SQLAlchemyAuditSink:
    session: Session
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    last_agent_run_id: str | None = None

    def record_agent_run(self, payload: dict) -> None:
        record = AgentRun(
            id=f"run-{uuid4()}",
            user_id=payload["user_id"],
            thread_id=payload["thread_id"],
            graph_name=payload["graph_name"],
            graph_version=payload["graph_version"],
            trigger_type=payload["trigger_type"],
            input_snapshot=payload,
            output_snapshot={"status": payload["status"]},
            status=payload["status"],
            latency_ms=payload["latency_ms"],
            error_message=payload.get("error_message"),
        )
        self.session.add(record)
        self.last_agent_run_id = record.id
        for tool_call in self.pending_tool_calls:
            tool_call.agent_run_id = record.id
        self.session.flush()

    def record_tool_call(self, payload: dict) -> None:
        record = ToolCall(
            id=f"tool-{uuid4()}",
            agent_run_id=self.last_agent_run_id,
            tool_name=payload["tool_name"],
            request_hash=sha256(str(payload.get("request_hash", "")).encode("utf-8")).hexdigest(),
            response_summary=payload.get("response_summary", {}),
            source_urls=payload.get("source_urls", []),
            status=payload["status"],
        )
        self.session.add(record)
        if record.agent_run_id is None:
            self.pending_tool_calls.append(record)
        self.session.flush()
