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
