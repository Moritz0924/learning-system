from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class ConversationError(ValueError):
    """Base error for ownership-safe conversation operations."""


class ConversationNotFound(ConversationError):
    pass


class ConversationThreadArchived(ConversationError):
    pass


class ActiveRunConflict(ConversationError):
    pass


class RunNotFound(ConversationError):
    pass


class ConversationThreadRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    user_id: str
    goal_id: str
    legacy_key: str | None
    title: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class AgentRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    user_id: str
    goal_id: str
    thread_id: str
    correlation_id: str
    request_hash: str
    graph_name: str
    graph_version: str
    trigger_type: str
    input_snapshot: dict
    output_snapshot: dict
    node_trace: list[dict]
    status: str
    latency_ms: int
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    cancel_requested_at: datetime | None
    cancelled_at: datetime | None


class ConversationRepository(Protocol):
    def create(
        self, *, user_id: str, goal_id: str, title: str | None
    ) -> ConversationThreadRecord: ...

    def get(
        self, *, user_id: str, goal_id: str, thread_id: str
    ) -> ConversationThreadRecord | None: ...

    def list(
        self, *, user_id: str, goal_id: str, include_archived: bool = False
    ) -> list[ConversationThreadRecord]: ...

    def archive(
        self, *, user_id: str, goal_id: str, thread_id: str
    ) -> ConversationThreadRecord: ...


class AgentRunRepository(Protocol):
    def find_by_id(self, *, run_id: str) -> AgentRunRecord | None: ...

    def list_for_thread(
        self, *, user_id: str, goal_id: str, thread_id: str
    ) -> list[AgentRunRecord]: ...

    def request_cancel_for_user(
        self, *, user_id: str, run_id: str
    ) -> AgentRunRecord: ...

    def request_cancel(
        self, *, user_id: str, goal_id: str, thread_id: str, run_id: str
    ) -> AgentRunRecord: ...

    def is_cancel_requested(
        self, *, user_id: str, goal_id: str, thread_id: str, run_id: str
    ) -> bool: ...
