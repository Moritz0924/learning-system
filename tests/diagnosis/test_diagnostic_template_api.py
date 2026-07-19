from __future__ import annotations

import json

from tests.conftest import register_user


def test_template_endpoint_requires_access_token(client) -> None:
    response = client.get("/api/onboarding/diagnostic-template?domain=ai_app_dev")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "auth.invalid_access_token"


def test_template_endpoint_returns_public_template_without_scoring_secrets(client) -> None:
    identity = register_user(client, email="template-reader@example.com")

    response = client.get(
        "/api/onboarding/diagnostic-template?domain=ai_app_dev",
        headers=identity["headers"],
    )

    assert response.status_code == 200
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)
    assert payload["template_version"] == "ai_app_dev_v1"
    assert payload["questions"][0]["node_code"]
    assert "correct_option_id" not in encoded
    assert "weight" not in encoded
    assert "difficulty" not in encoded
    assert "related_node_codes" not in encoded


def test_template_endpoint_maps_unknown_domain_to_stable_404(client) -> None:
    identity = register_user(client, email="template-missing@example.com")

    response = client.get(
        "/api/onboarding/diagnostic-template?domain=unknown",
        headers=identity["headers"],
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "diagnosis.template_not_found",
        "message": "The requested diagnostic template was not found.",
    }
