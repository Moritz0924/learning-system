from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.api.schemas.memories import MemoryListResponse, MemoryPublicResponse
from backend.app.application.memory_management_service import MemoryManagementService
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.domain.memory import (
    MemoryNotFound,
    MemoryPrivacySettings,
    MemoryScopeNotFound,
    MemoryType,
)


router = APIRouter(prefix="/api/memories", tags=["memories"])
SourceCategory = Literal["explicit_user_statement", "system_inference", "learning_result"]
MemoryStatus = Literal["active", "inactive", "all"]


@router.get("/privacy", response_model=MemoryPrivacySettings)
def get_memory_privacy_endpoint(
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> MemoryPrivacySettings:
    try:
        return MemoryManagementService(session).get_privacy(user_id=principal.user_id)
    except MemoryScopeNotFound as exc:
        raise _not_found() from exc


@router.put("/privacy", response_model=MemoryPrivacySettings)
def update_memory_privacy_endpoint(
    payload: MemoryPrivacySettings,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> MemoryPrivacySettings:
    try:
        result = MemoryManagementService(session).update_privacy(
            user_id=principal.user_id,
            settings=payload,
        )
        session.commit()
        return result
    except MemoryScopeNotFound as exc:
        session.rollback()
        raise _not_found() from exc
    except Exception:
        session.rollback()
        raise


@router.get("", response_model=MemoryListResponse)
def list_memories_endpoint(
    goal_id: str | None = None,
    memory_type: MemoryType | None = None,
    source_category: SourceCategory | None = None,
    status_filter: Annotated[MemoryStatus, Query(alias="status")] = "active",
    include_user_scope: bool = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> MemoryListResponse:
    return MemoryManagementService(session).list(
        user_id=principal.user_id,
        goal_id=goal_id,
        memory_type=memory_type,
        source_category=source_category,
        status=status_filter,
        include_user_scope=include_user_scope,
        limit=limit,
        offset=offset,
    )


@router.get("/{memory_id}", response_model=MemoryPublicResponse)
def get_memory_endpoint(
    memory_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> MemoryPublicResponse:
    try:
        return MemoryManagementService(session).get(user_id=principal.user_id, memory_id=memory_id)
    except MemoryNotFound as exc:
        raise _not_found() from exc


@router.post("/{memory_id}/disable", response_model=MemoryPublicResponse)
def disable_memory_endpoint(
    memory_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> MemoryPublicResponse:
    try:
        result = MemoryManagementService(session).disable(user_id=principal.user_id, memory_id=memory_id)
        session.commit()
        return result
    except MemoryNotFound as exc:
        session.rollback()
        raise _not_found() from exc
    except Exception:
        session.rollback()
        raise


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "memory.not_found", "message": "Memory was not found."},
    )
