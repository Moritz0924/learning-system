import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, ProgrammingError

from backend.app.routers import assessments, auth, config, documents, goals, health, memories, onboarding, plans, state, tasks, tools, tutor
from backend.app.application.conversation_service import (
    reconcile_archived_checkpoint_threads,
)
from backend.app.application.tool_approval_service import recover_stranded_tool_approvals
from backend.app.db import SessionLocal
from backend.app.infrastructure.checkpoints import (
    initialize_checkpoint_runtime,
    shutdown_checkpoint_runtime,
)
from backend.app.core.runtime_config import thread3_feature_flags

DEFAULT_CORS_ORIGINS = [
    "http://127.0.0.1:3000",
    "http://localhost:3000",
]
DEFAULT_DOCUMENT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_DOCUMENT_REQUEST_OVERHEAD_BYTES = 64 * 1024


class DocumentUploadRequestSizeLimitMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in {"/api/documents", "/api/documents/upload"}
        ):
            await self.app(scope, receive, send)
            return

        limit = _document_max_request_bytes(scope.get("path"))
        content_length = dict(scope.get("headers", [])).get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > limit:
                    await _send_document_request_too_large(scope, receive, send, limit)
                    return
            except ValueError:
                pass

        messages: list[dict[str, Any]] = []
        received_bytes = 0
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") != "http.request":
                break
            received_bytes += len(message.get("body", b""))
            if received_bytes > limit:
                await _send_document_request_too_large(scope, receive, send, limit)
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> dict[str, Any]:
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)


async def _send_document_request_too_large(scope, receive, send, limit: int) -> None:
    response = JSONResponse(
        status_code=413,
        content={"detail": f"document upload request exceeds {limit} byte limit"},
    )
    await response(scope, receive, send)


def _document_max_request_bytes(path: str | None = None) -> int:
    configured = os.getenv("DOCUMENT_MAX_REQUEST_BYTES")
    if configured:
        try:
            value = int(configured)
            if value > 0:
                return value
        except ValueError:
            pass
    try:
        upload_limit = int(os.getenv("DOCUMENT_MAX_UPLOAD_BYTES", str(DEFAULT_DOCUMENT_MAX_UPLOAD_BYTES)))
    except ValueError:
        upload_limit = DEFAULT_DOCUMENT_MAX_UPLOAD_BYTES
    if upload_limit <= 0:
        upload_limit = DEFAULT_DOCUMENT_MAX_UPLOAD_BYTES
    if path == "/api/documents":
        return upload_limit + DEFAULT_DOCUMENT_REQUEST_OVERHEAD_BYTES
    return ((upload_limit + 2) // 3 * 4) + DEFAULT_DOCUMENT_REQUEST_OVERHEAD_BYTES


def _cors_allowed_origins() -> list[str]:
    configured = [
        origin.strip()
        for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]
    return list(dict.fromkeys([*DEFAULT_CORS_ORIGINS, *configured]))


@asynccontextmanager
async def _lifespan(application: FastAPI):
    thread3_feature_flags()
    runtime = initialize_checkpoint_runtime()
    application.state.tutor_checkpoint_runtime = runtime
    try:
        with SessionLocal() as session:
            recover_stranded_tool_approvals(session)
            reconcile_archived_checkpoint_threads(session, runtime)
    except (OperationalError, ProgrammingError):
        # Existing readiness handling reports an unavailable or unmigrated DB.
        pass
    try:
        yield
    finally:
        shutdown_checkpoint_runtime()


app = FastAPI(title="Adaptive Private Tutor Stage 1", lifespan=_lifespan)
app.add_middleware(DocumentUploadRequestSizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(goals.router)
app.include_router(auth.router)
app.include_router(health.router)
app.include_router(onboarding.router)
app.include_router(state.router)
app.include_router(tutor.router)
app.include_router(memories.router)
app.include_router(assessments.router)
app.include_router(plans.router)
app.include_router(documents.router)
app.include_router(tasks.router)
app.include_router(tools.router)
app.include_router(config.router)


@app.exception_handler(ProgrammingError)
@app.exception_handler(OperationalError)
async def database_operational_error_handler(
    request: Request, exc: OperationalError | ProgrammingError
) -> JSONResponse:
    original_error = getattr(exc, "orig", None)
    original_message = str(original_error).lower()
    sqlstate = getattr(original_error, "sqlstate", None) or getattr(
        original_error, "pgcode", None
    )
    if sqlstate == "42P01" or original_message.startswith("no such table:"):
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Database schema is not migrated. Run "
                    "`.\\.venv\\Scripts\\python.exe -m alembic -c backend\\alembic.ini upgrade head` "
                    "before starting the API."
                )
            },
        )
    raise exc
