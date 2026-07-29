from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.domain.conversation import (
    ActiveRunConflict,
    AgentRunRecord,
    ConversationNotFound,
    ConversationThreadArchived,
    ConversationThreadRecord,
    RunNotFound,
)
from backend.app.models import AgentRun, ConversationThread, LearningGoal


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _thread_record(thread: ConversationThread) -> ConversationThreadRecord:
    return ConversationThreadRecord(
        id=thread.id,
        user_id=thread.user_id,
        goal_id=thread.goal_id,
        title=thread.title,
        status=thread.status,
        created_at=_as_utc(thread.created_at),
        updated_at=_as_utc(thread.updated_at),
        archived_at=_as_utc(thread.archived_at),
    )


def _run_record(run: AgentRun) -> AgentRunRecord:
    if run.goal_id is None or run.correlation_id is None or run.request_hash is None:
        raise RuntimeError("managed agent run is missing ownership or correlation fields")
    return AgentRunRecord(
        id=run.id,
        user_id=run.user_id,
        goal_id=run.goal_id,
        thread_id=run.thread_id,
        correlation_id=run.correlation_id,
        request_hash=run.request_hash,
        graph_name=run.graph_name,
        graph_version=run.graph_version,
        trigger_type=run.trigger_type,
        input_snapshot=run.input_snapshot or {},
        output_snapshot=run.output_snapshot or {},
        node_trace=run.node_trace or [],
        status=run.status,
        latency_ms=run.latency_ms,
        error_message=run.error_message,
        started_at=_as_utc(run.started_at),
        completed_at=_as_utc(run.completed_at),
        cancel_requested_at=_as_utc(run.cancel_requested_at),
        cancelled_at=_as_utc(run.cancelled_at),
    )


@dataclass
class SQLAlchemyConversationRepository:
    session: Session

    def create(
        self, *, user_id: str, goal_id: str, title: str | None
    ) -> ConversationThreadRecord:
        return self._create_with_id(
            user_id=user_id,
            goal_id=goal_id,
            thread_id=f"thread-{uuid4()}",
            title=title,
        )

    def ensure_legacy(
        self, *, user_id: str, goal_id: str, thread_id: str
    ) -> ConversationThreadRecord:
        existing = self.session.get(ConversationThread, thread_id)
        if existing is not None:
            if existing.user_id != user_id or existing.goal_id != goal_id:
                raise ConversationNotFound("Conversation thread was not found.")
            if existing.status != "active":
                raise ConversationThreadArchived("Conversation thread is archived.")
            return _thread_record(existing)
        try:
            with self.session.begin_nested():
                return self._create_with_id(
                    user_id=user_id,
                    goal_id=goal_id,
                    thread_id=thread_id,
                    title=None,
                )
        except IntegrityError:
            existing = self.session.get(ConversationThread, thread_id)
            if existing is None or existing.user_id != user_id or existing.goal_id != goal_id:
                raise ConversationNotFound("Conversation thread was not found.")
            if existing.status != "active":
                raise ConversationThreadArchived("Conversation thread is archived.")
            return _thread_record(existing)

    def _create_with_id(
        self, *, user_id: str, goal_id: str, thread_id: str, title: str | None
    ) -> ConversationThreadRecord:
        goal_exists = self.session.scalar(
            select(LearningGoal.id).where(
                LearningGoal.id == goal_id,
                LearningGoal.user_id == user_id,
            )
        )
        if goal_exists is None:
            raise ConversationNotFound("Conversation thread was not found.")
        now = _now()
        thread = ConversationThread(
            id=thread_id,
            user_id=user_id,
            goal_id=goal_id,
            title=title.strip() if title and title.strip() else None,
            status="active",
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        self.session.add(thread)
        self.session.flush()
        return _thread_record(thread)

    def get(
        self, *, user_id: str, goal_id: str, thread_id: str
    ) -> ConversationThreadRecord | None:
        thread = self.session.scalar(
            select(ConversationThread).where(
                ConversationThread.id == thread_id,
                ConversationThread.user_id == user_id,
                ConversationThread.goal_id == goal_id,
            )
        )
        return None if thread is None else _thread_record(thread)

    def require(
        self,
        *,
        user_id: str,
        goal_id: str,
        thread_id: str,
        active_only: bool = True,
    ) -> ConversationThreadRecord:
        thread = self.session.scalar(
            select(ConversationThread).where(
                ConversationThread.id == thread_id,
                ConversationThread.user_id == user_id,
                ConversationThread.goal_id == goal_id,
            )
        )
        if thread is None:
            raise ConversationNotFound("Conversation thread was not found.")
        if active_only and thread.status != "active":
            raise ConversationThreadArchived("Conversation thread is archived.")
        return _thread_record(thread)

    def list(
        self, *, user_id: str, goal_id: str, include_archived: bool = False
    ) -> list[ConversationThreadRecord]:
        statement = select(ConversationThread).where(
            ConversationThread.user_id == user_id,
            ConversationThread.goal_id == goal_id,
        )
        if not include_archived:
            statement = statement.where(ConversationThread.status == "active")
        statement = statement.order_by(
            ConversationThread.updated_at.desc(), ConversationThread.id.asc()
        )
        return [_thread_record(item) for item in self.session.scalars(statement)]

    def archive(
        self, *, user_id: str, goal_id: str, thread_id: str
    ) -> ConversationThreadRecord:
        thread = self.session.scalar(
            select(ConversationThread).where(
                ConversationThread.id == thread_id,
                ConversationThread.user_id == user_id,
                ConversationThread.goal_id == goal_id,
            )
        )
        if thread is None:
            raise ConversationNotFound("Conversation thread was not found.")
        if thread.status == "active":
            active_run = self.session.scalar(
                select(AgentRun.id).where(
                    AgentRun.thread_id == thread_id,
                    AgentRun.status.in_(("running", "cancellation_requested")),
                )
            )
            if active_run is not None:
                raise ActiveRunConflict("Conversation thread has an active run.")
            now = _now()
            thread.status = "archived"
            thread.archived_at = now
            thread.updated_at = now
            self.session.flush()
        return _thread_record(thread)


@dataclass
class SQLAlchemyAgentRunRepository:
    session: Session

    def start(
        self,
        *,
        user_id: str,
        goal_id: str,
        thread_id: str,
        correlation_id: str,
        request_hash: str,
        graph_name: str,
        graph_version: str,
        trigger_type: str,
        input_snapshot: dict,
    ) -> AgentRunRecord:
        SQLAlchemyConversationRepository(self.session).require(
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
        )
        existing = self.session.scalar(
            select(AgentRun.id).where(
                AgentRun.thread_id == thread_id,
                AgentRun.status.in_(("running", "cancellation_requested")),
            )
        )
        if existing is not None:
            raise ActiveRunConflict("Conversation thread already has an active run.")
        now = _now()
        run = AgentRun(
            id=f"run-{uuid4()}",
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            correlation_id=correlation_id,
            request_hash=request_hash,
            graph_name=graph_name,
            graph_version=graph_version,
            trigger_type=trigger_type,
            input_snapshot=input_snapshot,
            output_snapshot={},
            node_trace=[],
            status="running",
            latency_ms=0,
            error_message=None,
            started_at=now,
            completed_at=None,
            cancel_requested_at=None,
            cancelled_at=None,
            created_at=now.replace(tzinfo=None),
        )
        try:
            with self.session.begin_nested():
                self.session.add(run)
                self.session.flush()
        except IntegrityError as exc:
            raise ActiveRunConflict("Conversation thread already has an active run.") from exc
        return _run_record(run)

    def complete(
        self,
        *,
        user_id: str,
        goal_id: str,
        thread_id: str,
        run_id: str,
        output_snapshot: dict,
        node_trace: list[dict],
        latency_ms: int,
    ) -> AgentRunRecord:
        run = self._owned_run(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id, run_id=run_id
        )
        if run.status not in {"running", "cancellation_requested"}:
            return _run_record(run)
        run.output_snapshot = output_snapshot
        run.node_trace = node_trace
        run.latency_ms = max(0, latency_ms)
        run.status = "success"
        run.completed_at = _now()
        self.session.flush()
        return _run_record(run)

    def fail(
        self,
        *,
        user_id: str,
        goal_id: str,
        thread_id: str,
        run_id: str,
        error_message: str,
        node_trace: list[dict],
        latency_ms: int,
    ) -> AgentRunRecord:
        run = self._owned_run(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id, run_id=run_id
        )
        run.node_trace = node_trace
        run.latency_ms = max(0, latency_ms)
        run.error_message = error_message
        run.status = "failed"
        run.completed_at = _now()
        self.session.flush()
        return _run_record(run)

    def request_cancel(
        self, *, user_id: str, goal_id: str, thread_id: str, run_id: str
    ) -> AgentRunRecord:
        run = self._owned_run(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id, run_id=run_id
        )
        if run.status == "running":
            run.status = "cancellation_requested"
            run.cancel_requested_at = _now()
            self.session.flush()
        return _run_record(run)

    def is_cancel_requested(
        self, *, user_id: str, goal_id: str, thread_id: str, run_id: str
    ) -> bool:
        run = self._owned_run(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id, run_id=run_id
        )
        return run.cancel_requested_at is not None or run.status in {
            "cancellation_requested",
            "cancelled",
        }

    def mark_cancelled(
        self, *, user_id: str, goal_id: str, thread_id: str, run_id: str
    ) -> AgentRunRecord:
        run = self._owned_run(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id, run_id=run_id
        )
        if run.status in {"running", "cancellation_requested"}:
            now = _now()
            if run.cancel_requested_at is None:
                run.cancel_requested_at = now
            run.status = "cancelled"
            run.cancelled_at = now
            run.completed_at = now
            self.session.flush()
        return _run_record(run)

    def get(
        self, *, user_id: str, goal_id: str, thread_id: str, run_id: str
    ) -> AgentRunRecord | None:
        run = self.session.scalar(
            select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.user_id == user_id,
                AgentRun.goal_id == goal_id,
                AgentRun.thread_id == thread_id,
            )
        )
        return None if run is None else _run_record(run)

    def _owned_run(
        self, *, user_id: str, goal_id: str, thread_id: str, run_id: str
    ) -> AgentRun:
        run = self.session.scalar(
            select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.user_id == user_id,
                AgentRun.goal_id == goal_id,
                AgentRun.thread_id == thread_id,
            )
        )
        if run is None:
            raise RunNotFound("Agent run was not found.")
        return run
