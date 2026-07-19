from __future__ import annotations

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
from backend.app.core.runtime_config import runtime_mode
from backend.app.infrastructure.persistence.repositories.assessment_repository import SQLAlchemyAssessmentRepository
from backend.app.infrastructure.persistence.repositories.audit_repository import SQLAlchemyAuditSink
from backend.app.infrastructure.persistence.repositories.memory_repository import SQLAlchemyMemoryRepository
from backend.app.infrastructure.persistence.repositories.plan_repository import SQLAlchemyPlanRepository
from backend.app.infrastructure.persistence.repositories.rag_repository import SQLAlchemyRagRepository
from backend.app.infrastructure.persistence.repositories.state_repository import SQLAlchemyStateRepository
from backend.app.services.embeddings import build_embedding_client
from backend.app.services.llm_gateway import LLMGatewayClient
from backend.app.services.ocr import build_ocr_client


def _prepare_tutor_context(
    session: Session,
    request: TutorRunRequest,
) -> PreparedTutorContext:
    if request.trigger_type != "chat":
        raise ValueError("prepared tutor context is only valid for chat")
    embedding = build_embedding_client()
    state_repository = SQLAlchemyStateRepository(session)
    rag_repository = SQLAlchemyRagRepository(session, embedding)
    snapshot = state_repository.load_context(request.user_id, request.goal_id)
    memory_selection = MemoryContextService(SQLAlchemyMemoryRepository(session)).build(
        user_id=request.user_id,
        goal_id=request.goal_id,
        current_task=snapshot.get("current_task"),
    )
    chunks = rag_repository.retrieve(
        request.user_message,
        top_k=5,
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
        memory_selection=memory_selection,
    )


def _run_engine(
    session: Session,
    request: TutorRunRequest,
    *,
    prepared_context: PreparedTutorContext | None = None,
) -> TutorRunResult:
    embedding = build_embedding_client()
    llm_client = LLMGatewayClient()
    audit_sink = SQLAlchemyAuditSink(session)
    rag_repository = SQLAlchemyRagRepository(session, embedding)
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
    )
    started = perf_counter()
    try:
        engine = Phase2TutorEngine(dependencies)
        result = (
            engine.run(request, prepared_context=prepared_context)
            if prepared_context is not None
            else engine.run(request)
        )
        _execute_workflow_actions(result.workflow_actions, dependencies)
    except Exception as exc:
        if prepared_context is not None:
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
    return result

def _execute_workflow_actions(actions: list[WorkflowAction], dependencies: Phase2Dependencies) -> None:
    for action in actions:
        if action.action_type == "record_tool_call":
            dependencies.audit_sink.record_tool_call(action.audit_payload)
        elif action.action_type == "record_agent_run":
            dependencies.audit_sink.record_agent_run(action.audit_payload)
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
        else:
            raise RuntimeError(f"unsupported workflow action: {action.action_type}")

def _rag_runtime_mode(session: Session) -> str:
    bind = session.get_bind()
    if bind and bind.dialect.name == "postgresql" and runtime_mode("RAG_RETRIEVAL_BACKEND", default="pgvector") == "pgvector":
        return "pgvector"
    return "local_json_embedding"
