from __future__ import annotations

from tests.conftest import register_user, register_user_with_goal


DOCUMENT_STATUS_KEYS = {
    "id",
    "goal_id",
    "filename",
    "mime_type",
    "size_bytes",
    "parse_status",
    "parse_error_code",
    "parse_error",
    "page_count",
    "block_count",
    "parser_version",
    "created_at",
    "processing_started_at",
    "processing_completed_at",
}


def test_document_status_responses_are_safe_and_consistent(
    client, session_factory, monkeypatch
):
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "defer")
    account = register_user_with_goal(
        client, session_factory, email="document-status@example.com"
    )
    uploaded = client.post(
        "/api/documents",
        headers=account["headers"],
        data={"goal_id": account["goal_id"]},
        files={"file": ("notes.txt", b"status contract", "text/plain")},
    )

    assert uploaded.status_code == 201
    upload_body = uploaded.json()
    assert set(upload_body) == DOCUMENT_STATUS_KEYS
    assert upload_body["size_bytes"] == len(b"status contract")
    assert upload_body["goal_id"] == account["goal_id"]
    assert upload_body["parse_status"] == "pending"
    assert upload_body["parse_error_code"] is None
    assert upload_body["processing_started_at"] is None
    assert upload_body["processing_completed_at"] is None

    detail = client.get(
        f"/api/documents/{upload_body['id']}", headers=account["headers"]
    )
    listed = client.get("/api/documents", headers=account["headers"])

    assert detail.status_code == 200
    assert detail.json() == upload_body
    assert listed.status_code == 200
    assert listed.json() == {"documents": [upload_body]}
    serialized = str(upload_body).lower()
    assert "object_key" not in serialized
    assert "embedding" not in serialized
    assert "minio" not in serialized
    assert "provider" not in serialized


def test_document_list_is_newest_first(client, session_factory, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "defer")
    account = register_user_with_goal(
        client, session_factory, email="document-order@example.com"
    )
    for filename in ("first.txt", "second.txt"):
        response = client.post(
            "/api/documents",
            headers=account["headers"],
            data={"goal_id": account["goal_id"]},
            files={"file": (filename, filename.encode(), "text/plain")},
        )
        assert response.status_code == 201

    listed = client.get("/api/documents", headers=account["headers"]).json()

    assert [item["filename"] for item in listed["documents"]] == [
        "second.txt",
        "first.txt",
    ]


def test_multipart_document_upload_requires_token(client):
    response = client.post(
        "/api/documents",
        files={"file": ("notes.txt", b"not authenticated", "text/plain")},
    )

    assert response.status_code == 401

