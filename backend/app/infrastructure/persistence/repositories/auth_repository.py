from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.models import AuthSession, RefreshToken, User


class AuthRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_user_by_normalized_email(self, normalized_email: str) -> User | None:
        return self._session.scalar(select(User).where(User.normalized_email == normalized_email))

    def get_active_user(self, user_id: str) -> User | None:
        return self._session.scalar(select(User).where(User.id == user_id, User.status == "active"))

    def create_user(self, *, email: str, normalized_email: str, display_name: str, password_hash: str) -> User:
        user = User(
            id=f"user-{uuid4()}", email=email, normalized_email=normalized_email,
            display_name=display_name, password_hash=password_hash, status="active", role="learner", token_version=1,
        )
        self._session.add(user)
        self._session.flush()
        return user

    def create_session(self, *, user_id: str, idle_expires_at: datetime, absolute_expires_at: datetime, user_agent_hash: str | None) -> AuthSession:
        auth_session = AuthSession(
            id=str(uuid4()), user_id=user_id, status="active", idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at, user_agent_hash=user_agent_hash,
        )
        self._session.add(auth_session)
        self._session.flush()
        return auth_session

    def get_active_session_for_update(self, session_id: str) -> AuthSession | None:
        return self._session.scalar(select(AuthSession).where(AuthSession.id == session_id, AuthSession.status == "active").with_for_update())

    def get_refresh_token_for_update(self, token_id: str) -> RefreshToken | None:
        return self._session.scalar(select(RefreshToken).where(RefreshToken.id == token_id).with_for_update())

    def save_refresh_token(self, token: RefreshToken) -> None:
        self._session.add(token)
        self._session.flush()

    def revoke_session(self, *, session_id: str, reason: str, now: datetime) -> None:
        self._session.execute(update(AuthSession).where(AuthSession.id == session_id).values(status="revoked", revoked_at=now, revoke_reason=reason))
        self._session.execute(update(RefreshToken).where(RefreshToken.session_id == session_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now))

    def revoke_all_user_sessions(self, *, user_id: str, reason: str, now: datetime) -> None:
        ids = self._session.scalars(select(AuthSession.id).where(AuthSession.user_id == user_id, AuthSession.status == "active")).all()
        if not ids:
            return
        self._session.execute(update(AuthSession).where(AuthSession.id.in_(ids)).values(status="revoked", revoked_at=now, revoke_reason=reason))
        self._session.execute(update(RefreshToken).where(RefreshToken.session_id.in_(ids), RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
