from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from backend.app.domain.conversation import (
    AgentRunRecord,
    ConversationNotFound,
    ConversationThreadRecord,
)
from backend.app.infrastructure.persistence.repositories.conversation_repository import (
    SQLAlchemyAgentRunRepository,
    SQLAlchemyConversationRepository,
)

if TYPE_CHECKING:
    from backend.app.infrastructure.checkpoints import TutorCheckpointRuntime


@dataclass
class ConversationService:
    session: Session
    checkpoint_runtime: "TutorCheckpointRuntime | None" = None

    def create_thread(
        self, *, user_id: str, goal_id: str, title: str | None = None
    ) -> ConversationThreadRecord:
        return SQLAlchemyConversationRepository(self.session).create(
            user_id=user_id, goal_id=goal_id, title=title
        )

    def ensure_legacy_thread(
        self, *, user_id: str, goal_id: str, thread_id: str
    ) -> ConversationThreadRecord:
        return SQLAlchemyConversationRepository(self.session).ensure_legacy(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id
        )

    def get_thread(
        self, *, user_id: str, goal_id: str, thread_id: str
    ) -> ConversationThreadRecord:
        result = SQLAlchemyConversationRepository(self.session).get(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id
        )
        if result is None:
            raise ConversationNotFound("Conversation thread was not found.")
        return result

    def require_thread(
        self, *, user_id: str, goal_id: str, thread_id: str
    ) -> ConversationThreadRecord:
        return SQLAlchemyConversationRepository(self.session).require(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id
        )

    def list_threads(
        self,
        *,
        user_id: str,
        goal_id: str,
        include_archived: bool = False,
    ) -> list[ConversationThreadRecord]:
        return SQLAlchemyConversationRepository(self.session).list(
            user_id=user_id,
            goal_id=goal_id,
            include_archived=include_archived,
        )

    def archive_thread(
        self, *, user_id: str, goal_id: str, thread_id: str
    ) -> ConversationThreadRecord:
        archived = SQLAlchemyConversationRepository(self.session).archive(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id
        )
        runtime = self.checkpoint_runtime
        if runtime is None:
            from backend.app.infrastructure.checkpoints import active_checkpoint_runtime

            runtime = active_checkpoint_runtime()
        if runtime is not None:
            runtime.delete_thread(thread_id)
        return archived

    def request_run_cancellation(
        self, *, user_id: str, goal_id: str, thread_id: str, run_id: str
    ) -> AgentRunRecord:
        return SQLAlchemyAgentRunRepository(self.session).request_cancel(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id, run_id=run_id
        )

    def start_run(
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
        return SQLAlchemyAgentRunRepository(self.session).start(
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            correlation_id=correlation_id,
            request_hash=request_hash,
            graph_name=graph_name,
            graph_version=graph_version,
            trigger_type=trigger_type,
            input_snapshot=input_snapshot,
        )

    def complete_run(
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
        return SQLAlchemyAgentRunRepository(self.session).complete(
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            run_id=run_id,
            output_snapshot=output_snapshot,
            node_trace=node_trace,
            latency_ms=latency_ms,
        )

    def fail_run(
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
        return SQLAlchemyAgentRunRepository(self.session).fail(
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            run_id=run_id,
            error_message=error_message,
            node_trace=node_trace,
            latency_ms=latency_ms,
        )

    def is_run_cancellation_requested(
        self, *, user_id: str, goal_id: str, thread_id: str, run_id: str
    ) -> bool:
        return SQLAlchemyAgentRunRepository(self.session).is_cancel_requested(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id, run_id=run_id
        )

    def mark_run_cancelled(
        self, *, user_id: str, goal_id: str, thread_id: str, run_id: str
    ) -> AgentRunRecord:
        return SQLAlchemyAgentRunRepository(self.session).mark_cancelled(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id, run_id=run_id
        )
