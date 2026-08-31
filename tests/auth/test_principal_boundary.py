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


def _register(client, *, email: str = "principal@example.com"):
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
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


def test_goals_and_document_detail_are_scoped_to_the_principal(client, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    owner = _register(client, email="owner@example.com")
    other = _register(client, email="other@example.com")
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}

    initialized = client.post("/api/onboarding/initialize", headers=owner_headers, json=_onboarding_payload())
    assert initialized.status_code == 201
    listed = client.get("/api/goals", headers=owner_headers)
    assert listed.status_code == 200
    assert [goal["goal_id"] for goal in listed.json()["goals"]] == [initialized.json()["goal"]["goal_id"]]
    assert client.get("/api/goals", headers=other_headers).json() == {"goals": []}

    uploaded = client.post(
        "/api/documents/upload",
        headers=owner_headers,
        json={
            "goal_id": initialized.json()["goal"]["goal_id"],
            "filename": "notes.md",
            "mime_type": "text/markdown",
            "content": "private notes",
        },
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]
    detail = client.get(f"/api/documents/{document_id}", headers=owner_headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == document_id
    assert client.get(f"/api/documents/{document_id}", headers=other_headers).status_code == 404
