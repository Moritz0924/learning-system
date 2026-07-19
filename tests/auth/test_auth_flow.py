from __future__ import annotations


def test_register_creates_authenticated_session_without_returning_refresh_token(client, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://127.0.0.1:3000"},
        json={
            "email": "learner@example.com",
            "password": "correct horse battery staple",
            "display_name": "Learner",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "learner@example.com"
    assert "refresh" not in body
    assert "learning_refresh" in response.headers["set-cookie"]


def test_refresh_rotates_cookie_and_access_token_authenticates_private_endpoint(client, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    registered = client.post(
        "/api/auth/register",
        json={"email": "refresh@example.com", "password": "correct horse battery staple", "display_name": "Refresh"},
    )
    assert registered.status_code == 201

    refreshed = client.post("/api/auth/refresh")

    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] != registered.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"})
    assert me.status_code == 200
    assert me.json()["id"] == registered.json()["user"]["id"]


def test_register_returns_distinct_validation_codes(client, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")

    weak_password = client.post(
        "/api/auth/register",
        json={"email": "weak@example.com", "password": "too-short", "display_name": "Learner"},
    )
    invalid_name = client.post(
        "/api/auth/register",
        json={"email": "blank@example.com", "password": "correct horse battery staple", "display_name": "   "},
    )

    assert weak_password.status_code == 422
    assert weak_password.json()["detail"]["code"] == "auth.weak_password"
    assert invalid_name.status_code == 422
    assert invalid_name.json()["detail"]["code"] == "auth.invalid_display_name"


def test_register_returns_duplicate_email_conflict(client, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")
    payload = {"email": "duplicate@example.com", "password": "correct horse battery staple", "display_name": "Learner"}

    assert client.post("/api/auth/register", json=payload).status_code == 201
    duplicate = client.post("/api/auth/register", json=payload)

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "auth.email_already_registered"
