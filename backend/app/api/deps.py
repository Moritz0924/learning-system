from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app.core.principal import Principal
from backend.app.core.security import auth_settings
from backend.app.db import get_session
from backend.app.infrastructure.auth.jwt_codec import AccessTokenCodec, InvalidAccessToken
from backend.app.infrastructure.persistence.repositories.auth_repository import AuthRepository


bearer_scheme = HTTPBearer(auto_error=False)


def _invalid_access_token() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "auth.invalid_access_token", "message": "Authentication credentials are invalid or expired."},
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    session: Session = Depends(get_session),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _invalid_access_token()
    try:
        claims = AccessTokenCodec(auth_settings()).decode(credentials.credentials)
    except InvalidAccessToken as exc:
        raise _invalid_access_token() from exc
    repository = AuthRepository(session)
    user = repository.get_active_user(claims.user_id)
    auth_session = repository.get_active_session_for_update(claims.session_id)
    now = datetime.now(timezone.utc)
    if user is None or auth_session is None or auth_session.user_id != user.id or _as_utc(auth_session.idle_expires_at) <= now or _as_utc(auth_session.absolute_expires_at) <= now or user.token_version != claims.token_version:
        raise _invalid_access_token()
    role = user.role if user.role in {"learner", "admin"} else "learner"
    return Principal(user.id, auth_session.id, role, user.token_version, "access_jwt")


def require_role(*roles: str):
    def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "auth.insufficient_role"})
        return principal
    return dependency


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
