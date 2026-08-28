from __future__ import annotations

import os
from collections.abc import Callable
from time import perf_counter

from sqlalchemy.orm import Session

from adaptive_tutor.phase2.assessment import build_assessment_draft
from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.ports import Phase2Dependencies
from adaptive_tutor.phase2.schemas import (
    MemoryContextSelection,
    PreparedTutorContext,
    TutorRunRequest,
    TutorRunResult,
    WorkflowAction,
)
from backend.app.application.memory_context_service import MemoryContextService, build_tutor_context
from backend.app.application.memory_gate_service import decide_memory_candidates
from backend.app.application.memory_privacy_service import MemoryPrivacyService
from backend.app.application.memory_write_service import MemoryWriteService
from backend.app.application.conversation_service import ConversationService
from backend.app.domain.memory import MemoryWriteReceipt
from backend.app.core.runtime_config import runtime_mode, thread3_feature_flags
from backend.app.infrastructure.persistence.repositories.assessment_repository import SQLAlchemyAssessmentRepository
from backend.app.infrastructure.persistence.repositories.audit_repository import SQLAlchemyAuditSink
from backend.app.infrastructure.persistence.repositories.memory_repository import SQLAlchemyMemoryRepository
from backend.app.infrastructure.persistence.repositories.plan_repository import SQLAlchemyPlanRepository
from backend.app.infrastructure.persistence.repositories.rag_repository import SQLAlchemyRagRepository
from backend.app.infrastructure.persistence.repositories.state_repository import SQLAlchemyStateRepository
from backend.app.infrastructure.checkpoints import initialize_checkpoint_runtime
from backend.app.services.ocr import build_ocr_client
from adaptive_tutor.tutor.identifiers import new_run_id
from backend.app.services.tutor_tools import build_tutor_tool_router
from backend.app.application.config_service import RuntimeResolver, SkillSelection
from backend.app.application.mcp_service import McpApplicationService
from backend.app.application.tool_approval_service import ToolApprovalApplicationService
from backend.app.infrastructure.secrets import SecretStore
from backend.app.services.llm_gateway import LLMGatewayClient


DEFAULT_TUTOR_RAG_TOP_K = 5
MIN_TUTOR_RAG_TOP_K = 1
MAX_TUTOR_RAG_TOP_K = 10


def _tutor_rag_top_k() -> int:
    try:
        value = int(os.getenv("TUTOR_RAG_TOP_K", str(DEFAULT_TUTOR_RAG_TOP_K)))
    except ValueError:
        value = DEFAULT_TUTOR_RAG_TOP_K
    return min(max(value, MIN_TUTOR_RAG_TOP_K), MAX_TUTOR_RAG_TOP_K)


def _prepare_tutor_context(
    session: Session,
    request: TutorRunRequest,
    *,
    secret_store: SecretStore | None = None,
) -> PreparedTutorContext:
    if request.trigger_type != "chat":
        raise ValueError("prepared tutor context is only valid for chat")
    embedding = RuntimeResolver(
        session, user_id=request.user_id, secret_store=secret_store
    ).resolve("embedding")
    state_repository = SQLAlchemyStateRepository(session)
    rag_repository = SQLAlchemyRagRepository(session, embedding)
    snapshot = state_repository.load_context(request.user_id, request.goal_id)
    privacy_settings = MemoryPrivacyService(session).get(user_id=request.user_id)
    if request.memory_candidates:
        preflight_decisions = decide_memory_candidates(
            user_id=request.user_id,
            goal_id=request.goal_id,
            explicit_candidates=list(request.memory_candidates),
            assessment_result=None,
            mastery_updates=[],
            privacy_settings=privacy_settings,
        )
        MemoryWriteService(session).preflight_explicit_decisions(preflight_decisions)
    memory_selection = MemoryContextService(SQLAlchemyMemoryRepository(session)).build(
        user_id=request.user_id,
        goal_id=request.goal_id,
        current_task=snapshot.get("current_task"),
        privacy_settings=privacy_settings,
    )
    chunks = rag_repository.retrieve(
        request.user_message,
        top_k=_tutor_rag_top_k(),
        user_id=request.user_id,
    )
    return PreparedTutorContext(
        state_snapshot=snapshot,
        tutor_context=build_tutor_context(
            snapshot,
            memory_selection=memory_selection,
        ),
        retrieved_context=chunks,
        retrieval_status=rag_repository.last_retrieval_status,
        degraded_reason=rag_repository.degraded_reason,
        embedding_provider=getattr(embedding, "mode", "unknown"),
        retrieval_backend=_rag_runtime_mode(session),
        retrieval_run_id=new_run_id(),
        memory_selection=memory_selection,
        memory_privacy_settings=privacy_settings,
    )


def _run_engine(
    session: Session,
    request: TutorRunRequest,
    *,
    prepared_context: PreparedTutorContext | None = None,
    skip_agent_run_audit: bool = False,
    managed_run_id: str | None = None,
    before_chat_commit: Callable[[TutorRunResult], None] | None = None,
    after_chat_finalize: Callable[[TutorRunResult], None] | None = None,
    secret_store: SecretStore | None = None,
    skill_selection: SkillSelection = SkillSelection(),
    resume_value: object | None = None,
    teacher_delta_callback: Callable[[str], None] | None = None,
) -> TutorRunResult:
    resolver = RuntimeResolver(
        session,
        user_id=request.user_id,
        secret_store=secret_store,
        llm_factory=LLMGatewayClient,
    )
    embedding = resolver.resolve("embedding")
    if skill_selection.model_profile_id is not None:
        llm_client = resolver.resolve(
            skill_selection.capability or "chat",
            model_profile_id=skill_selection.model_profile_id,
            instruction_prompt=skill_selection.instruction_prompt,
        )
    elif request.trigger_type == "chat":
        llm_client = resolver.resolve_tutor_text(
            instruction_prompt=skill_selection.instruction_prompt,
        )
    else:
        llm_client = resolver.resolve(
            skill_selection.capability or "chat",
            instruction_prompt=skill_selection.instruction_prompt,
        )
    audit_sink = SQLAlchemyAuditSink(session, last_agent_run_id=managed_run_id)
    rag_repository = SQLAlchemyRagRepository(session, embedding)
    flags = thread3_feature_flags()
    tool_router = _build_runtime_tool_router(
        session,
        agent_run_id=managed_run_id,
        user_id=request.user_id,
        secret_store=secret_store,
        flags=flags,
    )
    approval_service = None
    if flags["FEATURE_MCP_TOOL_ROUTER_V2"] and managed_run_id is not None:
        approval_service = ToolApprovalApplicationService(
            session,
            user_id=request.user_id,
            mcp_service=McpApplicationService(
                session,
                user_id=request.user_id,
                secret_store=secret_store,
            ),
        )
    dependencies = Phase2Dependencies(
        state_repository=SQLAlchemyStateRepository(session),
        rag_repository=rag_repository,
        assessment_repository=SQLAlchemyAssessmentRepository(session, request.user_id, request.goal_id),
        plan_repository=SQLAlchemyPlanRepository(session),
        audit_sink=audit_sink,
        llm_client=llm_client,
        embedding_client=embedding,
        ocr_client=build_ocr_client(),
        assessment_factory=build_assessment_draft,
        tutor_context_factory=build_tutor_context,
        memory_gate=decide_memory_candidates,
        tool_router=tool_router,
        tool_approval_service=approval_service,
        approval_run_id=managed_run_id,
        teacher_delta_callback=teacher_delta_callback,
    )
    # Runtime binding and MCP catalog reads above may have opened a read
    # transaction. Keep the provider call outside it, as the legacy tutor
    # path guarantees.
    if prepared_context is not None:
        session.rollback()
    started = perf_counter()
    try:
        if request.trigger_type != "chat":
            ConversationService(session).require_thread(
                user_id=request.user_id,
                goal_id=request.goal_id,
                thread_id=request.thread_id,
            )
        checkpoint_runtime = initialize_checkpoint_runtime()
        engine = Phase2TutorEngine(
            dependencies,
            checkpointer=checkpoint_runtime.saver,
            history_policy=checkpoint_runtime.history_policy,
        )
        result = engine.run(
            request,
            prepared_context=prepared_context,
            defer_history_checkpoint=request.trigger_type == "chat",
            **({"resume_value": resume_value} if resume_value is not None else {}),
        )
        memory_receipts = _execute_workflow_actions(
            result.workflow_actions,
            dependencies,
            memory_writer=MemoryWriteService(session),
            skip_agent_run_audit=skip_agent_run_audit,
        )
        if request.trigger_type == "chat":
            if before_chat_commit is not None:
                before_chat_commit(result)
            session.commit()
            engine.finalize_chat_history(
                request,
                assistant_message=result.final_answer,
            )
            if after_chat_finalize is not None:
                after_chat_finalize(result)
                session.commit()
    except Exception as exc:
        if prepared_context is not None or managed_run_id is not None:
            session.rollback()
            raise
        failed_run = {
            "thread_id": request.thread_id,
            "user_id": request.user_id,
            "goal_id": request.goal_id,
            "graph_name": "phase2_tutor_graph",
            "graph_version": "phase2-v1",
            "trigger_type": request.trigger_type,
            "status": "failed",
            "latency_ms": int((perf_counter() - started) * 1000),
            "error_message": type(exc).__name__,
        }
        session.rollback()
        try:
            SQLAlchemyAuditSink(session).record_agent_run(failed_run)
            session.commit()
        except Exception:
            session.rollback()
        raise
    retrieval_status = (
        prepared_context.retrieval_status
        if prepared_context is not None
        else rag_repository.last_retrieval_status
    )
    degraded_reason = (
        prepared_context.degraded_reason
        if prepared_context is not None
        else rag_repository.degraded_reason
    )
    embedding_provider = (
        prepared_context.embedding_provider
        if prepared_context is not None
        else getattr(embedding, "mode", "unknown")
    )
    retrieval_backend = (
        prepared_context.retrieval_backend
        if prepared_context is not None
        else _rag_runtime_mode(session)
    )
    result.runtime_metadata = {
        **result.runtime_metadata,
        "llm": dict(llm_client.last_completion_metadata),
        "rag": {
            "mode": "unavailable" if retrieval_status == "failed" else "live",
            "retrieval_status": retrieval_status,
            "degraded_reason": degraded_reason,
            "citation_count": len(result.citations),
            "fallback_citations": False,
            "embedding_provider": embedding_provider,
            "retrieval_backend": retrieval_backend,
        },
        "t3": {"feature_flags": thread3_feature_flags()},
    }
    if request.trigger_type == "chat":
        memory_selection = (
            prepared_context.memory_selection
            if prepared_context is not None
            else MemoryContextSelection()
        )
        result.runtime_metadata["memory"] = {
            "selected_count": len(memory_selection.items),
            "skipped_by_budget": memory_selection.skipped_by_budget,
            "policy_version": memory_selection.policy_version,
        }
    if result.memory_decisions:
        result.runtime_metadata["memory_write"] = {
            "candidate_count": len(result.memory_decisions),
            "approved_count": sum(
                decision.decision == "approved" for decision in result.memory_decisions
            ),
            "saved_count": sum(
                receipt.status in {"saved", "reused"} for receipt in memory_receipts
            ),
            "rejected_count": sum(
                receipt.status == "rejected" for receipt in memory_receipts
            ),
            "conflict_count": sum(
                receipt.status == "conflict" for receipt in memory_receipts
            ),
            "policy_version": "memory-gate-v1",
        }
    return result


def _build_runtime_tool_router(
    session: Session,
    *,
    agent_run_id: str | None = None,
    user_id: str,
    secret_store: SecretStore | None,
    flags: dict[str, bool],
):
    if not (
        flags["FEATURE_MCP_TOOL_ROUTER_V2"]
        or flags["FEATURE_AGENT_TOOL_LOOP_V1"]
    ):
        return None
    return build_tutor_tool_router(
        session,
        agent_run_id=agent_run_id,
        user_id=user_id,
        secret_store=secret_store,
        include_mcp=flags["FEATURE_MCP_TOOL_ROUTER_V2"],
    )


def _resolve_tutor_request_thread(
    session: Session,
    request: TutorRunRequest,
) -> TutorRunRequest:
    """Persist a scoped application thread before any external checkpoint write."""
    try:
        thread = ConversationService(session).ensure_legacy_thread(
            user_id=request.user_id,
            goal_id=request.goal_id,
            thread_id=request.thread_id,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return request.model_copy(update={"thread_id": thread.id})


def _execute_workflow_actions(
    actions: list[WorkflowAction],
    dependencies: Phase2Dependencies,
    *,
    memory_writer: MemoryWriteService,
    skip_agent_run_audit: bool = False,
) -> list[MemoryWriteReceipt]:
    memory_receipts: list[MemoryWriteReceipt] = []
    for action in actions:
        if action.action_type == "record_tool_call":
            dependencies.audit_sink.record_tool_call(action.audit_payload)
        elif action.action_type == "record_agent_run":
            payload = _agent_run_payload_with_memory_receipts(
                action.audit_payload,
                memory_receipts,
            )
            if skip_agent_run_audit:
                action.audit_payload = payload
                continue
            dependencies.audit_sink.record_agent_run(payload)
        elif action.action_type == "save_assessment_draft":
            if action.assessment_draft is None:
                raise RuntimeError("save_assessment_draft action missing assessment_draft")
            dependencies.assessment_repository.save_assessment_draft(action.assessment_draft)
        elif action.action_type == "save_attempt_result":
            if action.assessment_result is None:
                raise RuntimeError("save_attempt_result action missing assessment_result")
            dependencies.assessment_repository.save_attempt_result(action.assessment_result)
        elif action.action_type == "save_mastery_updates":
            dependencies.assessment_repository.save_mastery_updates(action.mastery_updates)
        elif action.action_type == "save_plan_adjustment":
            if action.plan_adjustment is None:
                raise RuntimeError("save_plan_adjustment action missing plan_adjustment")
            dependencies.plan_repository.save_plan_adjustment(action.plan_adjustment)
        elif action.action_type == "refresh_state_snapshot":
            if not action.user_id or not action.goal_id:
                raise RuntimeError("refresh_state_snapshot action missing resource identity")
            dependencies.state_repository.refresh_snapshot(action.user_id, action.goal_id, action.snapshot_updates)
        elif action.action_type == "save_memory":
            if not action.user_id or not action.goal_id:
                raise RuntimeError("save_memory action missing resource identity")
            memory_receipts.extend(
                memory_writer.save_decisions(
                    user_id=action.user_id,
                    goal_id=action.goal_id,
                    decisions=action.memory_decisions,
                )
            )
        else:
            raise RuntimeError(f"unsupported workflow action: {action.action_type}")
    return memory_receipts


def _agent_run_payload_with_memory_receipts(
    payload: dict,
    receipts: list[MemoryWriteReceipt],
) -> dict:
    if not receipts or "memory_gate" not in payload:
        return payload
    receipts_by_candidate = {receipt.candidate_id: receipt for receipt in receipts}
    result = {**payload, "memory_gate": dict(payload["memory_gate"])}
    result["memory_gate"]["items"] = [
        {
            **item,
            "status": receipts_by_candidate[item["candidate_id"]].status,
            "write_reason_code": receipts_by_candidate[item["candidate_id"]].reason_code,
            "memory_id": receipts_by_candidate[item["candidate_id"]].memory_id,
        }
        for item in payload["memory_gate"]["items"]
    ]
    return result


def _rag_runtime_mode(session: Session) -> str:
    bind = session.get_bind()
    if bind and bind.dialect.name == "postgresql" and runtime_mode("RAG_RETRIEVAL_BACKEND", default="pgvector") == "pgvector":
        return "pgvector"
    return "local_json_embedding"
