from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.application.auth_service import AuthService
from backend.app.core.security import auth_settings
from backend.app.models import AuthSession, RefreshToken, User


def test_setting_legacy_password_revokes_existing_sessions_and_enables_login(db_session, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    now = datetime.now(timezone.utc)
    user = User(
        id="legacy-user",
        email="Legacy@example.com",
        normalized_email="legacy@example.com",
        display_name="Legacy",
        password_hash=None,
        status="active",
        role="learner",
        token_version=1,
    )
    db_session.add(user)
    db_session.commit()
    db_session.add(
        AuthSession(
            id="legacy-session",
            user_id=user.id,
            status="active",
            idle_expires_at=now + timedelta(days=1),
            absolute_expires_at=now + timedelta(days=2),
        )
    )
    db_session.add(
        RefreshToken(
            id="legacy-refresh",
            session_id="legacy-session",
            token_hash="not-a-real-token-hash",
            expires_at=now + timedelta(days=1),
        )
    )
    db_session.commit()

    service = AuthService(db_session, auth_settings())
    service.set_legacy_password(email=" LEGACY@example.com ", password="correct horse battery staple")

    db_session.expire_all()
    updated_user = db_session.get(User, user.id)
    updated_session = db_session.get(AuthSession, "legacy-session")
    updated_refresh = db_session.get(RefreshToken, "legacy-refresh")
    assert updated_user is not None
    assert updated_user.password_hash is not None
    assert updated_user.token_version == 2
    assert updated_session.status == "revoked"
    assert updated_refresh.revoked_at is not None
    assert service.login(
        email="legacy@example.com",
        password="correct horse battery staple",
        user_agent=None,
    ).user.id == user.id
