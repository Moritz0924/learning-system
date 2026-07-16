from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.security import AuthSettings
from backend.app.infrastructure.auth.jwt_codec import AccessTokenCodec
from backend.app.infrastructure.auth.password_hasher import PasswordHasher
from backend.app.infrastructure.auth.refresh_tokens import GeneratedRefreshToken, RefreshTokenFactory
from backend.app.infrastructure.persistence.repositories.auth_repository import AuthRepository
from backend.app.models import LearnerProfile, RefreshToken, User


class AuthError(ValueError):
    code = "auth.invalid_credentials"


class InvalidCredentials(AuthError):
    pass


class EmailAlreadyRegistered(AuthError):
    code = "auth.email_already_registered"


class WeakPassword(AuthError):
    code = "auth.weak_password"


class InvalidDisplayName(AuthError):
    code = "auth.invalid_display_name"


class RegistrationConflict(AuthError):
    code = "auth.registration_conflict"


class InvalidRefresh(AuthError):
    code = "auth.invalid_refresh_token"


class RefreshRace(AuthError):
    code = "auth.refresh_race"


@dataclass(frozen=True)
class AuthResult:
    access_token: str
    expires_in: int
    user: User
    refresh_cookie_value: str


class AuthService:
    def __init__(self, session: Session, settings: AuthSettings) -> None:
        self._session = session
        self._settings = settings
        self._repository = AuthRepository(session)
        self._hasher = PasswordHasher()
        self._tokens = RefreshTokenFactory()
        self._codec = AccessTokenCodec(settings)

    def register(self, *, email: str, password: str, display_name: str, user_agent: str | None) -> AuthResult:
        normalized = _normalize_email(email)
        if self._repository.get_user_by_normalized_email(normalized):
            raise EmailAlreadyRegistered("email already registered")
        if len(password) < 12:
            raise WeakPassword("Password must contain at least 12 characters.")
        if not display_name.strip():
            raise InvalidDisplayName("Display name must not be blank.")
        now = _now()
        try:
            user = self._repository.create_user(email=email.strip(), normalized_email=normalized, display_name=display_name.strip(), password_hash=self._hasher.hash(password))
            user.password_changed_at = now
            self._session.add(LearnerProfile(user_id=user.id, privacy_settings={"data_scope": "v1"}))
            result = self._create_session_result(user=user, now=now, user_agent=user_agent)
            self._session.commit()
            return result
        except IntegrityError as exc:
            self._session.rollback()
            if self._repository.get_user_by_normalized_email(normalized):
                raise EmailAlreadyRegistered("email already registered") from exc
            raise RegistrationConflict("Registration could not be completed.") from exc
        except Exception:
            self._session.rollback()
            raise

    def login(self, *, email: str, password: str, user_agent: str | None) -> AuthResult:
        user = self._repository.get_user_by_normalized_email(_normalize_email(email))
        valid = self._hasher.verify_or_dummy(password, user.password_hash if user else None)
        if user is None or not valid or user.status != "active":
            raise InvalidCredentials()
        now = _now()
        try:
            user.last_login_at = now
            result = self._create_session_result(user=user, now=now, user_agent=user_agent)
            self._session.commit()
            return result
        except Exception:
            self._session.rollback()
            raise

    def set_legacy_password(self, *, email: str, password: str) -> User:
        """Activate a historical account without exposing a public claim flow."""
        if len(password) < 12:
            raise AuthError("password does not meet requirements")
        user = self._repository.get_user_by_normalized_email(_normalize_email(email))
        if user is None or user.status != "active":
            raise AuthError("active user not found")
        if user.password_hash is not None:
            raise AuthError("password credentials already exist")
        now = _now()
        try:
            user.password_hash = self._hasher.hash(password)
            user.password_changed_at = now
            user.token_version += 1
            self._repository.revoke_all_user_sessions(
                user_id=user.id,
                reason="legacy_password_activated",
                now=now,
            )
            self._session.commit()
            return user
        except Exception:
            self._session.rollback()
            raise

    def refresh(self, *, cookie_value: str | None, user_agent: str | None) -> AuthResult:
        parsed = self._tokens.parse(cookie_value)
        if parsed is None:
            raise InvalidRefresh()
        token_id, secret = parsed
        token = self._repository.get_refresh_token_for_update(token_id)
        now = _now()
        if token is None or not self._tokens.verify(presented_secret=secret, expected_hash=token.token_hash):
            raise InvalidRefresh()
        auth_session = self._repository.get_active_session_for_update(token.session_id)
        if auth_session is None or token.revoked_at or _as_utc(token.expires_at) <= now or _as_utc(auth_session.idle_expires_at) <= now or _as_utc(auth_session.absolute_expires_at) <= now:
            raise InvalidRefresh()
        if token.used_at:
            age = (now - token.used_at).total_seconds()
            if age <= self._settings.refresh_rotation_grace_seconds and _agent_hash(user_agent) == auth_session.user_agent_hash:
                raise RefreshRace()
            token.reuse_detected_at = now
            self._repository.revoke_session(session_id=auth_session.id, reason="refresh_token_reuse", now=now)
            self._session.commit()
            raise InvalidRefresh()
        user = self._repository.get_active_user(auth_session.user_id)
        if user is None:
            raise InvalidRefresh()
        replacement = self._tokens.generate()
        refreshed_expiry = min(now + timedelta(seconds=self._settings.refresh_idle_ttl_seconds), _as_utc(auth_session.absolute_expires_at))
        self._repository.save_refresh_token(RefreshToken(id=replacement.token_id, session_id=auth_session.id, token_hash=replacement.token_hash, parent_token_id=token.id, expires_at=refreshed_expiry))
        token.used_at = now
        token.replaced_by_token_id = replacement.token_id
        auth_session.last_seen_at = now
        auth_session.idle_expires_at = refreshed_expiry
        access, _ = self._codec.issue(user_id=user.id, session_id=auth_session.id, role=user.role, token_version=user.token_version)
        self._session.commit()
        return AuthResult(access, self._settings.access_ttl_seconds, user, replacement.cookie_value)

    def logout(self, *, session_id: str | None) -> None:
        if session_id:
            self._repository.revoke_session(session_id=session_id, reason="logout", now=_now())
            self._session.commit()

    def logout_all(self, *, user_id: str) -> None:
        user = self._repository.get_active_user(user_id)
        if user is None:
            return
        user.token_version += 1
        self._repository.revoke_all_user_sessions(user_id=user_id, reason="logout_all", now=_now())
        self._session.commit()

    def _create_session_result(self, *, user: User, now: datetime, user_agent: str | None) -> AuthResult:
        absolute = now + timedelta(seconds=self._settings.refresh_absolute_ttl_seconds)
        idle = min(now + timedelta(seconds=self._settings.refresh_idle_ttl_seconds), absolute)
        auth_session = self._repository.create_session(user_id=user.id, idle_expires_at=idle, absolute_expires_at=absolute, user_agent_hash=_agent_hash(user_agent))
        refresh = self._tokens.generate()
        self._repository.save_refresh_token(RefreshToken(id=refresh.token_id, session_id=auth_session.id, token_hash=refresh.token_hash, expires_at=idle))
        access, _ = self._codec.issue(user_id=user.id, session_id=auth_session.id, role=user.role, token_version=user.token_version)
        return AuthResult(access, self._settings.access_ttl_seconds, user, refresh.cookie_value)


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _agent_hash(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
