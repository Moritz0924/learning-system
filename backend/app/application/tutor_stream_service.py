from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from time import perf_counter

from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import PreparedTutorContext, TutorRunRequest, TutorRunResult
from adaptive_tutor.tutor.identifiers import new_run_id, stable_request_hash
from backend.app.application.conversation_service import ConversationService
from backend.app.application.engine import _prepare_tutor_context, _run_engine
from backend.app.application.learning_activity_service import _load_goal_for_user
from backend.app.application.serialization import _run_result_to_dict
from backend.app.domain.conversation import AgentRunRecord
from backend.app.domain.memory import MemoryCandidate


class TutorRunCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class StreamingTutorRun:
    request: TutorRunRequest
    run: AgentRunRecord


def begin_streaming_tutor_run(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    thread_id: str,
    message: str,
    memory_candidate: MemoryCandidate | None = None,
) -> StreamingTutorRun:
    request = TutorRunRequest(
        trigger_type="chat",
        user_id=user_id,
        goal_id=goal_id,
        thread_id=thread_id,
        user_message=message,
        memory_candidates=[] if memory_candidate is None else [memory_candidate],
    )
    try:
        _load_goal_for_user(session, user_id=user_id, goal_id=goal_id)
        ConversationService(session).require_thread(
            user_id=user_id, goal_id=goal_id, thread_id=thread_id
        )
        run = ConversationService(session).start_run(
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            correlation_id=new_run_id(),
            request_hash=stable_request_hash(request),
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={
                "source": "tutor_chat_stream",
                "goal_id": goal_id,
                "thread_id": thread_id,
                "has_memory_declaration": memory_candidate is not None,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return StreamingTutorRun(request=request, run=run)


def prepare_streaming_context(
    session: Session, streaming_run: StreamingTutorRun
) -> PreparedTutorContext:
    try:
        prepared = _prepare_tutor_context(session, streaming_run.request)
        session.rollback()
        return prepared
    except Exception:
        session.rollback()
        raise


def execute_streaming_tutor_run(
    session: Session,
    streaming_run: StreamingTutorRun,
    *,
    prepared_context: PreparedTutorContext,
    disconnected: Event,
) -> TutorRunResult:
    started = perf_counter()
    service = ConversationService(session)

    def cancel_before_commit(result: TutorRunResult) -> None:
        if disconnected.is_set() or service.is_run_cancellation_requested(
            user_id=streaming_run.request.user_id,
            goal_id=streaming_run.request.goal_id,
            thread_id=streaming_run.request.thread_id,
            run_id=streaming_run.run.id,
        ):
            service.mark_run_cancelled(
                user_id=streaming_run.request.user_id,
                goal_id=streaming_run.request.goal_id,
                thread_id=streaming_run.request.thread_id,
                run_id=streaming_run.run.id,
            )
            raise TutorRunCancelled

    def complete_after_checkpoint(result: TutorRunResult) -> None:
        completed = service.complete_run(
            user_id=streaming_run.request.user_id,
            goal_id=streaming_run.request.goal_id,
            thread_id=streaming_run.request.thread_id,
            run_id=streaming_run.run.id,
            output_snapshot=_public_result(result),
            node_trace=list(result.audit_log),
            latency_ms=int((perf_counter() - started) * 1000),
        )
        if completed.status == "cancelled":
            raise TutorRunCancelled

    return _run_engine(
        session,
        streaming_run.request,
        prepared_context=prepared_context,
        skip_agent_run_audit=True,
        before_chat_commit=cancel_before_commit,
        after_chat_finalize=complete_after_checkpoint,
    )


def finish_streaming_failure(
    session: Session,
    streaming_run: StreamingTutorRun,
    *,
    error: Exception,
    disconnected: Event,
) -> str:
    service = ConversationService(session)
    try:
        if disconnected.is_set() or service.is_run_cancellation_requested(
            user_id=streaming_run.request.user_id,
            goal_id=streaming_run.request.goal_id,
            thread_id=streaming_run.request.thread_id,
            run_id=streaming_run.run.id,
        ):
            terminal = service.mark_run_cancelled(
                user_id=streaming_run.request.user_id,
                goal_id=streaming_run.request.goal_id,
                thread_id=streaming_run.request.thread_id,
                run_id=streaming_run.run.id,
            )
        else:
            terminal = service.fail_run(
                user_id=streaming_run.request.user_id,
                goal_id=streaming_run.request.goal_id,
                thread_id=streaming_run.request.thread_id,
                run_id=streaming_run.run.id,
                error_message=type(error).__name__,
                node_trace=[],
                latency_ms=0,
            )
        session.commit()
        return terminal.status
    except Exception:
        session.rollback()
        raise


def _public_result(result: TutorRunResult) -> dict:
    serialized = _run_result_to_dict(result)
    return {
        "final_answer": serialized["final_answer"],
        "citations": [
            {
                "citation_label": item.get("citation_label"),
                "source_title": item.get("source_title"),
                "source_url": item.get("source_url"),
            }
            for item in serialized["citations"]
        ],
        "runtime_metadata": _public_runtime_metadata(serialized.get("runtime_metadata", {})),
    }


def public_stream_result(result: TutorRunResult) -> dict:
    return _public_result(result)


def _public_runtime_metadata(metadata: object) -> dict:
    if not isinstance(metadata, dict):
        return {}
    allowed: dict[str, tuple[str, ...]] = {
        "llm": ("mode", "is_remote", "model"),
        "rag": (
            "mode",
            "retrieval_status",
            "citation_count",
            "fallback_citations",
            "embedding_provider",
            "retrieval_backend",
        ),
        "memory": ("selected_count", "skipped_by_budget", "policy_version"),
        "memory_write": (
            "candidate_count",
            "approved_count",
            "saved_count",
            "rejected_count",
            "conflict_count",
            "policy_version",
        ),
    }
    public = {}
    for section, keys in allowed.items():
        value = metadata.get(section)
        if isinstance(value, dict):
            public[section] = {key: value[key] for key in keys if key in value}
    return public
