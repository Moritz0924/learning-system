from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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


def _project_run_messages(run: AgentRunRecord) -> list[dict[str, Any]]:
    request = run.input_snapshot.get("request")
    question = request.get("user_message") if isinstance(request, dict) else None
    if not isinstance(question, str) or not question:
        return []
    messages: list[dict[str, Any]] = [
        {
            "id": f"{run.id}:user",
            "run_id": run.id,
            "role": "user",
            "content": question,
            "created_at": run.started_at,
        }
    ]
    answer = run.output_snapshot.get("final_answer")
    if run.status != "success" or not isinstance(answer, str) or not answer:
        return messages
    assistant: dict[str, Any] = {
        "id": f"{run.id}:assistant",
        "run_id": run.id,
        "role": "assistant",
        "content": answer,
        "created_at": run.completed_at or run.started_at,
        "citations": run.output_snapshot.get("citations")
        if isinstance(run.output_snapshot.get("citations"), list)
        else [],
    }
    grounding_status = run.output_snapshot.get("grounding_status")
    if isinstance(grounding_status, str):
        assistant["grounding_status"] = grounding_status
    messages.append(assistant)
    return messages


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

    def list_messages(
        self,
        *,
        user_id: str,
        goal_id: str,
        thread_id: str,
        limit: int,
        before: str | None,
    ) -> dict[str, Any]:
        self.get_thread(user_id=user_id, goal_id=goal_id, thread_id=thread_id)
        runs = SQLAlchemyAgentRunRepository(self.session)
        cursor_id = None
        if before is not None:
            run_id, separator, role = before.rpartition(":")
            if not separator or not run_id or role not in {"user", "assistant"}:
                raise ValueError("Invalid transcript cursor.")
            cursor = runs.find_by_id(run_id=run_id)
            if cursor is None:
                raise ValueError("Invalid transcript cursor.")
            if (
                cursor.user_id != user_id
                or cursor.goal_id != goal_id
                or cursor.thread_id != thread_id
            ):
                raise ConversationNotFound("Conversation transcript was not found.")
            cursor_id = before

        messages = [
            message
            for run in runs.list_for_thread(
                user_id=user_id,
                goal_id=goal_id,
                thread_id=thread_id,
            )
            for message in _project_run_messages(run)
        ]
        if cursor_id is not None:
            try:
                cursor_index = next(
                    index
                    for index, message in enumerate(messages)
                    if message["id"] == cursor_id
                )
            except StopIteration as exc:
                raise ValueError("Invalid transcript cursor.") from exc
            messages = messages[:cursor_index]
        has_older = len(messages) > limit
        page = messages[-limit:]
        return {
            "messages": page,
            "next_before": page[0]["id"] if has_older and page else None,
        }

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
