from __future__ import annotations

from backend.app.models import User

def _onboarding_payload(**extra):
    return {
        "title": "Build an AI application",
        "target_outcome": "Ship a private tutor",
        "deadline": "2026-08-15",
        "weekly_hours_target": 8,
        "learning_preferences": {"style": "coach_then_code"},
        "available_slots": {},
        "self_assessment": {"python_level": 3},
        "submitted_answers": {"questions": []},
        **extra,
    }


def _register(client):
    response = client.post(
        "/api/auth/register",
        json={
            "email": "principal@example.com",
            "password": "correct horse battery staple",
            "display_name": "Principal",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_onboarding_does_not_authenticate_x_user_id_header(client, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")

    response = client.post(
        "/api/onboarding/initialize",
        headers={"X-User-Id": "forged-user"},
        json=_onboarding_payload(
            user_id="forged-user",
            email="forged@example.com",
            display_name="Forged",
        ),
    )

    assert response.status_code == 401


def test_onboarding_rejects_client_identity_fields_for_authenticated_user(client, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    registered = _register(client)

    response = client.post(
        "/api/onboarding/initialize",
        headers={"Authorization": f"Bearer {registered['access_token']}"},
        json=_onboarding_payload(user_id=registered["user"]["id"]),
    )

    assert response.status_code == 422


def test_user_model_derives_normalized_email_before_persistence(db_session):
    user = User(id="normalized-user", email=" Normalized@Example.com ", display_name="Normalized", status="active")
    db_session.add(user)
    db_session.commit()

    assert user.normalized_email == "normalized@example.com"
