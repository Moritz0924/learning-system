from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import event, select
from sqlalchemy.orm import Session, SessionTransaction

from backend.app.domain.conversation import (
    AgentRunRecord,
    ConversationNotFound,
    ConversationThreadRecord,
)
from backend.app.infrastructure.persistence.repositories.conversation_repository import (
    SQLAlchemyAgentRunRepository,
    SQLAlchemyConversationRepository,
)
from backend.app.models import ConversationThread

if TYPE_CHECKING:
    from backend.app.infrastructure.checkpoints import TutorCheckpointRuntime


_CHECKPOINT_CLEANUP_INTENTS = "tutor_checkpoint_cleanup_intents"


@event.listens_for(Session, "after_commit")
def _delete_committed_checkpoint_threads(session: Session) -> None:
    intents_by_transaction = session.info.get(_CHECKPOINT_CLEANUP_INTENTS, {})
    nested = session.get_nested_transaction()
    if nested is not None:
        committed = intents_by_transaction.pop(nested, {})
        if committed and nested.parent is not None:
            intents_by_transaction.setdefault(nested.parent, {}).update(committed)
        _discard_empty_intent_registry(session, intents_by_transaction)
        return

    root = session.get_transaction()
    committed = intents_by_transaction.pop(root, {})
    _discard_empty_intent_registry(session, intents_by_transaction)
    for thread_id, runtime in committed.items():
        runtime.schedule_thread_deletion(thread_id)


@event.listens_for(Session, "after_rollback")
def _discard_rolled_back_checkpoint_cleanup(session: Session) -> None:
    intents_by_transaction = session.info.get(_CHECKPOINT_CLEANUP_INTENTS, {})
    nested = session.get_nested_transaction()
    if nested is not None:
        intents_by_transaction.pop(nested, None)
        _discard_empty_intent_registry(session, intents_by_transaction)
        return
    session.info.pop(_CHECKPOINT_CLEANUP_INTENTS, None)


@event.listens_for(Session, "after_transaction_end")
def _discard_ended_checkpoint_cleanup(
    session: Session,
    transaction: SessionTransaction,
) -> None:
    intents_by_transaction = session.info.get(_CHECKPOINT_CLEANUP_INTENTS, {})
    intents_by_transaction.pop(transaction, None)
    _discard_empty_intent_registry(session, intents_by_transaction)


def _discard_empty_intent_registry(
    session: Session,
    intents_by_transaction: dict,
) -> None:
    if not intents_by_transaction:
        session.info.pop(_CHECKPOINT_CLEANUP_INTENTS, None)


def reconcile_archived_checkpoint_threads(
    session: Session,
    checkpoint_runtime: "TutorCheckpointRuntime",
) -> int:
    thread_ids = list(
        session.scalars(
            select(ConversationThread.id).where(
                ConversationThread.status == "archived"
            )
        )
    )
    for thread_id in thread_ids:
        checkpoint_runtime.schedule_thread_deletion(thread_id)
    return len(thread_ids)


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
            transaction = (
                self.session.get_nested_transaction()
                or self.session.get_transaction()
            )
            if transaction is None:
                raise RuntimeError(
                    "conversation archive cleanup requires an active transaction"
                )
            intents_by_transaction = self.session.info.setdefault(
                _CHECKPOINT_CLEANUP_INTENTS,
                {},
            )
            intents = intents_by_transaction.setdefault(transaction, {})
            intents[thread_id] = runtime
        return archived

    def request_run_cancellation(
        self, *, user_id: str, goal_id: str, thread_id: str, run_id: str
    ) -> AgentRunRecord:
        return SQLAlchemyAgentRunRepository(self.session).request_cancel(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id, run_id=run_id
        )

    def request_owned_run_cancellation(
        self, *, user_id: str, run_id: str
    ) -> AgentRunRecord:
        return SQLAlchemyAgentRunRepository(self.session).request_cancel_for_user(
            user_id=user_id, run_id=run_id
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
        input_snapshot: dict | None = None,
        output_snapshot: dict,
        node_trace: list[dict],
        latency_ms: int,
    ) -> AgentRunRecord:
        return SQLAlchemyAgentRunRepository(self.session).complete(
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            run_id=run_id,
            input_snapshot=input_snapshot,
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
