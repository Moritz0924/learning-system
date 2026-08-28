from __future__ import annotations

from threading import Event
from time import perf_counter

from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import TutorRunRequest
from adaptive_tutor.tutor.identifiers import new_run_id, stable_request_hash
from backend.app.domain.memory import MemoryCandidate
from backend.app.application.config_service import resolve_skill_selection
from backend.app.application.engine import _prepare_tutor_context, _run_engine
from backend.app.application.conversation_service import ConversationService
from backend.app.application.learning_activity_service import _load_goal_for_user
from backend.app.application.serialization import _run_result_to_dict
from backend.app.application.tutor_stream_service import (
    StreamingTutorRun,
    TutorRunCancelled,
    finish_streaming_failure,
    managed_run_input_snapshot,
    public_stream_result,
)
from backend.app.infrastructure.secrets import SecretStore


def answer_tutor_question(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    thread_id: str,
    message: str,
    locale: str = "en-US",
    model_tier: str | None = None,
    skill_ids: list[str] | None = None,
    secret_store: SecretStore | None = None,
    memory_candidate: MemoryCandidate | None = None,
) -> dict:
    skill_selection = resolve_skill_selection(
        session,
        user_id,
        skill_ids,
        secret_store=secret_store,
        context_chars_used=len(message),
    )
    request = TutorRunRequest(
        trigger_type="chat",
        user_id=user_id,
        goal_id=goal_id,
        thread_id=thread_id,
        user_message=message,
        skill_ids=skill_ids,
        metadata={
            "locale": locale,
            **({} if model_tier is None else {"model_tier": model_tier}),
        },
        memory_candidates=[] if memory_candidate is None else [memory_candidate],
    )
    managed_run: StreamingTutorRun | None = None
    started = perf_counter()
    try:
        _load_goal_for_user(session, user_id=user_id, goal_id=goal_id)
        thread = ConversationService(session).ensure_legacy_thread(
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
        )
        request = request.model_copy(update={"thread_id": thread.id})
        initial_input_snapshot = {
            "source": "tutor_chat_sync",
            "goal_id": goal_id,
            "thread_id": thread.id,
            "has_memory_declaration": memory_candidate is not None,
        }
        run = ConversationService(session).start_run(
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread.id,
            correlation_id=new_run_id(),
            request_hash=stable_request_hash(request),
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot=initial_input_snapshot,
        )
        session.commit()
        managed_run = StreamingTutorRun(request=request, run=run)
        prepared_context = (
            _prepare_tutor_context(session, request)
            if secret_store is None
            else _prepare_tutor_context(session, request, secret_store=secret_store)
        )
        session.rollback()

        def cancel_before_commit(result) -> None:
            service = ConversationService(session)
            if service.is_run_cancellation_requested(
                user_id=user_id,
                goal_id=goal_id,
                thread_id=thread.id,
                run_id=run.id,
            ):
                service.mark_run_cancelled(
                    user_id=user_id,
                    goal_id=goal_id,
                    thread_id=thread.id,
                    run_id=run.id,
                )
                raise TutorRunCancelled

        def complete_after_checkpoint(result) -> None:
            completed = ConversationService(session).complete_run(
                user_id=user_id,
                goal_id=goal_id,
                thread_id=thread.id,
                run_id=run.id,
                input_snapshot=managed_run_input_snapshot(
                    result,
                    initial=initial_input_snapshot,
                ),
                output_snapshot=public_stream_result(result),
                node_trace=list(result.audit_log),
                latency_ms=int((perf_counter() - started) * 1000),
            )
            if completed.status == "cancelled":
                raise TutorRunCancelled

        engine_options = {
            "prepared_context": prepared_context,
            "skip_agent_run_audit": True,
            "managed_run_id": run.id,
            "before_chat_commit": cancel_before_commit,
            "after_chat_finalize": complete_after_checkpoint,
        }
        if secret_store is not None or skill_selection.skill_ids:
            engine_options.update(
                secret_store=secret_store,
                skill_selection=skill_selection,
            )
        result = _run_engine(session, request, **engine_options)
    except Exception as exc:
        session.rollback()
        if managed_run is not None:
            finish_streaming_failure(
                session,
                managed_run,
                error=exc,
                disconnected=Event(),
            )
        raise
    return _run_result_to_dict(result)
