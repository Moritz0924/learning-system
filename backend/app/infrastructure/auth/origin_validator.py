from __future__ import annotations

from fastapi import HTTPException, Request, status

from backend.app.core.runtime_config import is_production_environment
from backend.app.core.security import AuthSettings


def require_allowed_origin(request: Request, settings: AuthSettings) -> None:
    if not settings.require_origin_check:
        return
    origin = request.headers.get("origin")
    if origin is None and not is_production_environment():
        return
    if origin not in settings.allowed_origins:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "auth.invalid_origin"})
