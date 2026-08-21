import json
from threading import Event

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse
from fastapi.responses import JSONResponse

from backend.app.api.deps import get_current_principal
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.application.tutor_service import answer_tutor_question
from backend.app.application.feedback_service import submit_tutor_feedback
from backend.app.api.schemas.tutor import (
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationResponse,
    LearningPreferenceDeclaration,
    LongTermGoalDeclaration,
    RunCancellationResponse,
    ToolApprovalDecisionRequest,
    TutorChatRequest,
    TutorFeedbackRequest,
)
from backend.app.application.conversation_service import ConversationService
from backend.app.application.tutor_stream_service import (
    TutorRunCancelled,
    begin_streaming_tutor_run,
    begin_tool_approval_resume,
    execute_streaming_tutor_run,
    execute_streaming_tutor_resume,
    finish_streaming_failure,
    prepare_streaming_context,
    prepare_tool_approval_resume,
    public_stream_result,
)
from backend.app.application.memory_candidate_service import (
    build_explicit_goal_candidate,
    build_explicit_preference_candidate,
)
from backend.app.domain.memory import MemoryGateInvariantError, MemoryIdempotencyConflict
from backend.app.domain.conversation import (
    ActiveRunConflict,
    ConversationError,
    ConversationNotFound,
    ConversationThreadArchived,
    RunNotFound,
)
from backend.app.core.exceptions import FeedbackIdempotencyConflict
from backend.app.application.config_service import (
    RuntimeResolutionError,
    SkillSelectionInvalid,
    SkillSelectionNotFound,
)
from backend.app.infrastructure.secrets import SecretStore
from backend.app.routers.config import get_secret_store
from backend.app.services.llm_gateway import EvaluationProviderError
from backend.app.application.mcp_service import McpApplicationService
from backend.app.application.tool_approval_service import (
    ToolApprovalApplicationService,
    ToolApprovalConflict,
    ToolApprovalNotFound,
)
from adaptive_tutor.phase2.engine import TutorRunAwaitingApproval


router = APIRouter(prefix="/api/tutor", tags=["tutor"])


@router.post("/runs/{run_id}/feedback", status_code=201)
def tutor_feedback_endpoint(
    run_id: str,
    payload: TutorFeedbackRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    try:
        result = submit_tutor_feedback(
            session,
            user_id=principal.user_id,
            run_id=run_id,
            helpful=payload.helpful,
            citation_correct=payload.citation_correct,
            difficulty_fit=payload.difficulty_fit,
            reason_code=payload.reason_code,
            optional_comment=payload.optional_comment,
        )
        return JSONResponse(status_code=200 if result["replayed"] else 201, content=result)
    except FeedbackIdempotencyConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/chat")
def tutor_chat_endpoint(
    payload: TutorChatRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
) -> dict:
    try:
        memory_candidate = _memory_candidate(payload, user_id=principal.user_id)
    except ValidationError as exc:
        raise _invalid_memory_declaration() from exc
    try:
        return answer_tutor_question(
            session,
            user_id=principal.user_id,
            goal_id=payload.goal_id,
            thread_id=payload.thread_id,
            message=payload.message,
            model_tier=payload.model_tier,
            skill_ids=payload.skill_ids,
            secret_store=store,
            memory_candidate=memory_candidate,
        )
    except MemoryIdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "memory.idempotency_conflict",
                "message": "The memory request identifier was already used with different content.",
            },
        ) from exc
    except MemoryGateInvariantError as exc:
        raise _invalid_memory_declaration() from exc
    except SkillSelectionNotFound as exc:
        raise _invalid_skill_not_found() from exc
    except SkillSelectionInvalid as exc:
        raise _invalid_skill_selection() from exc
    except RuntimeResolutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": "The configured model is unavailable."},
        ) from exc
    except EvaluationProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "runtime.provider_call_failed", "message": "The configured model call failed."},
        ) from exc
    except ActiveRunConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (LookupError, ConversationError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/conversations", response_model=ConversationResponse, status_code=201)
def create_conversation_endpoint(
    payload: ConversationCreateRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> ConversationResponse:
    try:
        thread = ConversationService(session).create_thread(
            user_id=principal.user_id,
            goal_id=payload.goal_id,
            title=payload.title,
        )
        session.commit()
        return _conversation_response(thread)
    except ConversationError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/conversations", response_model=ConversationListResponse)
def list_conversations_endpoint(
    goal_id: str = Query(min_length=1, max_length=255),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> ConversationListResponse:
    conversations = ConversationService(session).list_threads(
        user_id=principal.user_id,
        goal_id=goal_id,
    )
    return ConversationListResponse(
        conversations=[_conversation_response(item) for item in conversations]
    )


@router.delete("/conversations/{thread_id}", status_code=204)
def delete_conversation_endpoint(
    thread_id: str,
    goal_id: str = Query(min_length=1, max_length=255),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> Response:
    try:
        ConversationService(session).archive_thread(
            user_id=principal.user_id,
            goal_id=goal_id,
            thread_id=thread_id,
        )
        session.commit()
        return Response(status_code=204)
    except ConversationNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ActiveRunConflict as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/runs/{run_id}/cancel", response_model=RunCancellationResponse, status_code=202)
def cancel_tutor_run_endpoint(
    run_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> RunCancellationResponse:
    try:
        run = ConversationService(session).request_owned_run_cancellation(
            user_id=principal.user_id,
            run_id=run_id,
        )
        session.commit()
        return RunCancellationResponse(run_id=run.id, status=run.status)
    except RunNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/tool-approvals")
def list_tool_approvals_endpoint(
    thread_id: str = Query(min_length=1, max_length=255),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
) -> dict:
    service = ToolApprovalApplicationService(
        session,
        user_id=principal.user_id,
        mcp_service=McpApplicationService(session, user_id=principal.user_id, secret_store=store),
    )
    return {"approvals": service.list_for_thread(thread_id=thread_id)}


@router.post("/runs/{run_id}/tool-approvals/{approval_id}/decision")
def decide_tool_approval_endpoint(
    run_id: str,
    approval_id: str,
    payload: ToolApprovalDecisionRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
) -> StreamingResponse:
    try:
        prepare_tool_approval_resume(
            session,
            user_id=principal.user_id,
            run_id=run_id,
            approval_id=approval_id,
            secret_store=store,
        )
    except ToolApprovalNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "mcp.approval_not_found"}) from exc
    except ToolApprovalConflict as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.payload) from exc
    except (ValueError, RuntimeResolutionError, SkillSelectionNotFound, SkillSelectionInvalid) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "mcp.approval_not_resumable"}) from exc

    disconnected = Event()

    async def stream_events():
        monitor_done = anyio.Event()
        terminalized = False

        async def monitor_disconnect() -> None:
            while not monitor_done.is_set():
                if await request.is_disconnected():
                    disconnected.set()
                    return
                await anyio.sleep(0.05)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(monitor_disconnect)
            try:
                streaming_run, accepted = await anyio.to_thread.run_sync(
                    lambda: begin_tool_approval_resume(
                        session,
                        user_id=principal.user_id,
                        run_id=run_id,
                        approval_id=approval_id,
                        decision=payload.decision,
                        secret_store=store,
                    )
                )
                if accepted.decision == "approve":
                    yield _sse("tool.started", {"run_id": run_id, "approval_id": approval_id})
                result = await anyio.to_thread.run_sync(
                    lambda: execute_streaming_tutor_resume(
                        session,
                        streaming_run,
                        approval_decision=accepted,
                        disconnected=disconnected,
                    )
                )
                terminalized = True
                public_result = public_stream_result(result)
                yield _sse(
                    "tool.completed",
                    {
                        "run_id": run_id,
                        "approval_id": approval_id,
                        "status": "completed" if accepted.decision == "approve" else "rejected",
                    },
                )
                yield _sse("teacher.delta", {"delta": public_result["final_answer"]})
                yield _sse("run.completed", {"result": public_result})
            except ToolApprovalConflict as exc:
                terminalized = True
                yield _sse(
                    "run.failed",
                    {"run_id": run_id, "code": exc.payload["code"], "message": "The tool approval is no longer pending."},
                )
            except TutorRunCancelled as exc:
                await anyio.to_thread.run_sync(
                    lambda: finish_streaming_failure(session, streaming_run, error=exc, disconnected=disconnected)
                )
                terminalized = True
                yield _sse("run.cancelled", {"run_id": run_id})
            except Exception as exc:
                terminal_status = await anyio.to_thread.run_sync(
                    lambda: finish_streaming_failure(session, streaming_run, error=exc, disconnected=disconnected)
                )
                terminalized = True
                yield _sse(
                    "run.cancelled" if terminal_status == "cancelled" else "run.failed",
                    {
                        "run_id": run_id,
                        "code": getattr(exc, "code", "mcp.resume_failed"),
                        "message": "The tool approval could not be resumed.",
                    },
                )
            finally:
                if not terminalized:
                    disconnected.set()
                monitor_done.set()
                tasks.cancel_scope.cancel()

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/stream")
def tutor_chat_stream_endpoint(
    payload: TutorChatRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
) -> StreamingResponse:
    try:
        memory_candidate = _memory_candidate(payload, user_id=principal.user_id)
        streaming_run = begin_streaming_tutor_run(
            session,
            user_id=principal.user_id,
            goal_id=payload.goal_id,
            thread_id=payload.thread_id,
            message=payload.message,
            model_tier=payload.model_tier,
            skill_ids=payload.skill_ids,
            secret_store=store,
            memory_candidate=memory_candidate,
        )
    except ValidationError as exc:
        raise _invalid_memory_declaration() from exc
    except MemoryGateInvariantError as exc:
        raise _invalid_memory_declaration() from exc
    except MemoryIdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "memory.idempotency_conflict",
                "message": "The memory request identifier was already used with different content.",
            },
        ) from exc
    except SkillSelectionNotFound as exc:
        raise _invalid_skill_not_found() from exc
    except SkillSelectionInvalid as exc:
        raise _invalid_skill_selection() from exc
    except RuntimeResolutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": "The configured model is unavailable."},
        ) from exc
    except ActiveRunConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (LookupError, ConversationNotFound, ConversationThreadArchived) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    disconnected = Event()

    async def stream_events():
        monitor_done = anyio.Event()
        terminalized = False

        async def monitor_disconnect() -> None:
            while not monitor_done.is_set():
                if await request.is_disconnected():
                    disconnected.set()
                    return
                await anyio.sleep(0.05)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(monitor_disconnect)
            try:
                yield _sse(
                    "run.started",
                    {"run_id": streaming_run.run.id, "thread_id": payload.thread_id},
                )
                yield _sse("node.started", {"node": "retrieval"})
                prepared_context = await anyio.to_thread.run_sync(
                    prepare_streaming_context, session, streaming_run
                )
                yield _sse(
                    "retrieval.completed",
                    {
                        "citation_count": len(prepared_context.retrieved_context),
                        "status": prepared_context.retrieval_status,
                    },
                )
                yield _sse("node.completed", {"node": "retrieval"})
                yield _sse("node.started", {"node": "teacher"})
                result = await anyio.to_thread.run_sync(
                    lambda: execute_streaming_tutor_run(
                        session,
                        streaming_run,
                        prepared_context=prepared_context,
                        disconnected=disconnected,
                    )
                )
                terminalized = True
                public_result = public_stream_result(result)
                yield _sse(
                    "teacher.delta", {"delta": public_result["final_answer"]}
                )
                yield _sse("node.completed", {"node": "teacher"})
                yield _sse("run.completed", {"result": public_result})
            except TutorRunAwaitingApproval as exc:
                terminalized = True
                yield _sse("tool.approval_required", exc.payload)
                yield _sse(
                    "run.awaiting_approval",
                    {"run_id": streaming_run.run.id, "approval_id": exc.payload["approval_id"]},
                )
            except TutorRunCancelled as exc:
                await anyio.to_thread.run_sync(
                    lambda: finish_streaming_failure(
                        session,
                        streaming_run,
                        error=exc,
                        disconnected=disconnected,
                    )
                )
                terminalized = True
                yield _sse("run.cancelled", {"run_id": streaming_run.run.id})
            except Exception as exc:
                terminal_status = await anyio.to_thread.run_sync(
                    lambda: finish_streaming_failure(
                        session,
                        streaming_run,
                        error=exc,
                        disconnected=disconnected,
                    )
                )
                terminalized = True
                if terminal_status == "cancelled":
                    yield _sse("run.cancelled", {"run_id": streaming_run.run.id})
                else:
                    failure_code = (
                        "runtime.provider_call_failed"
                        if isinstance(exc, EvaluationProviderError)
                        else exc.code
                        if isinstance(exc, RuntimeResolutionError)
                        else "tutor.run_failed"
                    )
                    yield _sse(
                        "run.failed",
                        {
                            "run_id": streaming_run.run.id,
                            "code": failure_code,
                            "message": "The tutor run could not be completed.",
                        },
                    )
            finally:
                if not terminalized:
                    disconnected.set()
                    with anyio.CancelScope(shield=True):
                        try:
                            await anyio.to_thread.run_sync(
                                lambda: finish_streaming_failure(
                                    session,
                                    streaming_run,
                                    error=TutorRunCancelled(),
                                    disconnected=disconnected,
                                )
                            )
                        except Exception:
                            session.rollback()
                monitor_done.set()
                tasks.cancel_scope.cancel()

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _conversation_response(thread) -> ConversationResponse:
    return ConversationResponse(
        thread_id=thread.id,
        goal_id=thread.goal_id,
        title=thread.title,
        status=thread.status,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


def _memory_candidate(payload: TutorChatRequest, *, user_id: str):
    if isinstance(payload.memory_declaration, LearningPreferenceDeclaration):
        return build_explicit_preference_candidate(
            user_id=user_id,
            request_id=payload.memory_declaration.request_id,
            preference_key=payload.memory_declaration.preference_key,
            preference_value=payload.memory_declaration.preference_value,
        )
    if isinstance(payload.memory_declaration, LongTermGoalDeclaration):
        return build_explicit_goal_candidate(
            user_id=user_id,
            goal_id=payload.goal_id,
            request_id=payload.memory_declaration.request_id,
            title=payload.memory_declaration.title,
            target_outcome=payload.memory_declaration.target_outcome,
            deadline=payload.memory_declaration.deadline,
        )
    return None


def _sse(event_type: str, data: dict) -> str:
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _invalid_memory_declaration() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "memory.invalid_declaration",
            "message": "The structured memory declaration is invalid.",
        },
    )


def _invalid_skill_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "skill.not_found", "message": "A selected skill was not found."},
    )


def _invalid_skill_selection() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "skill.invalid", "message": "The selected skills are invalid."},
    )
