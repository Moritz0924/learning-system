from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from time import perf_counter
from collections.abc import Callable

from sqlalchemy.orm import Session
from sqlalchemy import select

from adaptive_tutor.phase2.schemas import PreparedTutorContext, TutorRunRequest, TutorRunResult
from adaptive_tutor.tutor.identifiers import new_run_id, stable_request_hash
from backend.app.application.conversation_service import ConversationService
from backend.app.application.engine import _prepare_tutor_context, _run_engine
from backend.app.application.learning_activity_service import _load_goal_for_user
from backend.app.application.serialization import _run_result_to_dict
from backend.app.domain.conversation import AgentRunRecord
from backend.app.domain.memory import MemoryCandidate
from backend.app.application.config_service import SkillSelection, resolve_skill_selection
from backend.app.application.mcp_service import McpApplicationService
from backend.app.application.tool_approval_service import (
    ToolApprovalApplicationService,
    ToolApprovalDecision,
)
from backend.app.infrastructure.secrets import SecretStore
from backend.app.models import AgentRun
from backend.app.infrastructure.persistence.repositories.state_repository import SQLAlchemyStateRepository


class TutorRunCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class StreamingTutorRun:
    request: TutorRunRequest
    run: AgentRunRecord
    skill_selection: SkillSelection = SkillSelection()
    secret_store: SecretStore | None = None


def begin_streaming_tutor_run(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    thread_id: str,
    task_id: str | None = None,
    message: str,
    locale: str = "en-US",
    model_tier: str | None = None,
    skill_ids: list[str] | None = None,
    secret_store: SecretStore | None = None,
    memory_candidate: MemoryCandidate | None = None,
) -> StreamingTutorRun:
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
        task_id=task_id,
        user_message=message,
        skill_ids=skill_ids,
        metadata={
            "locale": locale,
            **({} if model_tier is None else {"model_tier": model_tier}),
        },
        memory_candidates=[] if memory_candidate is None else [memory_candidate],
    )
    try:
        _load_goal_for_user(session, user_id=user_id, goal_id=goal_id)
        if task_id is not None:
            SQLAlchemyStateRepository(session).load_context(
                user_id,
                goal_id,
                task_id=task_id,
            )
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
                "task_id": task_id,
                "request": request.model_dump(mode="json"),
                "has_memory_declaration": memory_candidate is not None,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return StreamingTutorRun(
        request=request,
        run=run,
        skill_selection=skill_selection,
        secret_store=secret_store,
    )


def prepare_tool_approval_resume(
    session: Session,
    *,
    user_id: str,
    run_id: str,
    approval_id: str,
    secret_store: SecretStore | None = None,
) -> StreamingTutorRun:
    run = session.scalar(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id)
    )
    if run is None:
        from backend.app.application.tool_approval_service import ToolApprovalNotFound

        raise ToolApprovalNotFound("tool approval was not found")
    raw_request = (run.input_snapshot or {}).get("request")
    if not isinstance(raw_request, dict):
        raise ValueError("approval run cannot be resumed")
    request = TutorRunRequest.model_validate(raw_request)
    selection = resolve_skill_selection(
        session,
        user_id,
        request.skill_ids,
        secret_store=secret_store,
        context_chars_used=len(request.user_message),
    )
    approval_service = ToolApprovalApplicationService(
        session,
        user_id=user_id,
        mcp_service=McpApplicationService(
            session,
            user_id=user_id,
            secret_store=secret_store,
        ),
    )
    approval_service.preview_decision(run_id=run_id, approval_id=approval_id)
    return StreamingTutorRun(
        request=request,
        run=run,
        skill_selection=selection,
        secret_store=secret_store,
    )


def begin_tool_approval_resume(
    session: Session,
    *,
    user_id: str,
    run_id: str,
    approval_id: str,
    decision: str,
    secret_store: SecretStore | None = None,
) -> tuple[StreamingTutorRun, ToolApprovalDecision]:
    streaming_run = prepare_tool_approval_resume(
        session,
        user_id=user_id,
        run_id=run_id,
        approval_id=approval_id,
        secret_store=secret_store,
    )
    accepted = ToolApprovalApplicationService(
        session,
        user_id=user_id,
        mcp_service=McpApplicationService(session, user_id=user_id, secret_store=secret_store),
    ).begin_decision(
        run_id=run_id,
        approval_id=approval_id,
        decision=decision,  # type: ignore[arg-type]
    )
    return streaming_run, accepted


def prepare_streaming_context(
    session: Session, streaming_run: StreamingTutorRun
) -> PreparedTutorContext:
    try:
        prepared = _prepare_tutor_context(
            session,
            streaming_run.request,
            secret_store=streaming_run.secret_store,
        )
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
    on_teacher_delta: Callable[[str], None] | None = None,
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
            input_snapshot=managed_run_input_snapshot(
                result,
                initial=streaming_run.run.input_snapshot,
            ),
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
        managed_run_id=streaming_run.run.id,
        before_chat_commit=cancel_before_commit,
        after_chat_finalize=complete_after_checkpoint,
        secret_store=streaming_run.secret_store,
        skill_selection=streaming_run.skill_selection,
        teacher_delta_callback=on_teacher_delta,
    )


def execute_streaming_tutor_resume(
    session: Session,
    streaming_run: StreamingTutorRun,
    *,
    approval_decision: ToolApprovalDecision,
    disconnected: Event,
    on_teacher_delta: Callable[[str], None] | None = None,
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
            input_snapshot=managed_run_input_snapshot(
                result,
                initial=streaming_run.run.input_snapshot,
            ),
            output_snapshot=_public_result(result),
            node_trace=list(result.audit_log),
            latency_ms=int((perf_counter() - started) * 1000),
        )
        if completed.status == "cancelled":
            raise TutorRunCancelled

    return _run_engine(
        session,
        streaming_run.request,
        skip_agent_run_audit=True,
        managed_run_id=streaming_run.run.id,
        before_chat_commit=cancel_before_commit,
        after_chat_finalize=complete_after_checkpoint,
        secret_store=streaming_run.secret_store,
        skill_selection=streaming_run.skill_selection,
        teacher_delta_callback=on_teacher_delta,
        resume_value={
            "approval_id": approval_decision.approval_id,
            "decision": approval_decision.decision,
        },
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
    citations = serialized.get("public_citations") or serialized["citations"]
    payload = {
        "final_answer": serialized["final_answer"],
        "citations": [
            {
                "citation_id": item.get("citation_id"),
                "title": item.get("title") or item.get("source_title"),
                "source_type": item.get("source_type"),
                "excerpt": item.get("excerpt"),
                "citation_label": item.get("citation_label"),
                "source_title": item.get("source_title"),
                "source_url": item.get("source_url"),
            }
            for item in citations
        ],
        "runtime_metadata": _public_runtime_metadata(serialized.get("runtime_metadata", {})),
    }
    metadata = serialized.get("runtime_metadata", {})
    rag_metadata = metadata.get("rag", {}) if isinstance(metadata, dict) else {}
    payload["retrieval_backend"] = (
        rag_metadata.get("retrieval_backend") if isinstance(rag_metadata, dict) else None
    )
    if serialized.get("grounding_status") is not None:
        payload.update(
            {
                "grounding_status": serialized["grounding_status"],
                "insufficient_evidence": serialized.get("insufficient_evidence", False),
                "missing_information": serialized.get("missing_information", []),
            }
        )
    return payload


def public_stream_result(result: TutorRunResult) -> dict:
    return _public_result(result)


def managed_run_input_snapshot(
    result: TutorRunResult,
    *,
    initial: dict,
) -> dict:
    for action in result.workflow_actions:
        if action.action_type == "record_agent_run":
            return {
                **action.audit_payload,
                **initial,
                "thread_id": initial["thread_id"],
            }
    return dict(initial)


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
