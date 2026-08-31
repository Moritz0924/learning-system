from __future__ import annotations

from backend.app.models import Document, LearningGoal
from tests.conftest import register_user


def _seed_goal(session_factory, *, user_id: str, goal_id: str) -> None:
    with session_factory() as session:
        session.add(
            LearningGoal(
                id=goal_id,
                user_id=user_id,
                title=goal_id,
                target_outcome=f"Complete {goal_id}",
                weekly_hours_target=4,
            )
        )
        session.commit()


def _upload(client, identity: dict, *, goal_id: str, filename: str):
    return client.post(
        "/api/documents",
        headers=identity["headers"],
        data={"goal_id": goal_id},
        files={"file": (filename, f"content for {filename}".encode(), "text/plain")},
    )


def test_json_and_multipart_upload_require_an_owned_goal(
    client, session_factory, monkeypatch
) -> None:
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "defer")
    owner = register_user(client, email="document-goal-owner@example.com")
    other = register_user(client, email="document-goal-other@example.com")
    _seed_goal(session_factory, user_id=owner["user_id"], goal_id="goal-owner")
    _seed_goal(session_factory, user_id=other["user_id"], goal_id="goal-other")

    missing_multipart = client.post(
        "/api/documents",
        headers=owner["headers"],
        files={"file": ("missing.txt", b"missing goal", "text/plain")},
    )
    missing_json = client.post(
        "/api/documents/upload",
        headers=owner["headers"],
        json={"filename": "missing.md", "content": "missing goal"},
    )
    foreign = _upload(
        client,
        owner,
        goal_id="goal-other",
        filename="foreign.txt",
    )
    multipart = _upload(
        client,
        owner,
        goal_id="goal-owner",
        filename="owned.txt",
    )
    json_upload = client.post(
        "/api/documents/upload",
        headers=owner["headers"],
        json={
            "goal_id": "goal-owner",
            "filename": "owned.md",
            "mime_type": "text/markdown",
            "content": "# Owned",
        },
    )

    assert missing_multipart.status_code == 422
    assert missing_json.status_code == 422
    assert foreign.status_code == 404
    assert multipart.status_code == 201, multipart.text
    assert multipart.json()["goal_id"] == "goal-owner"
    assert json_upload.status_code == 201, json_upload.text
    assert json_upload.json()["goal_id"] == "goal-owner"


def test_list_filter_and_assignment_are_owner_and_goal_scoped(
    client, session_factory, monkeypatch
) -> None:
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "defer")
    owner = register_user(client, email="document-assign-owner@example.com")
    other = register_user(client, email="document-assign-other@example.com")
    for goal_id in ("goal-a", "goal-b"):
        _seed_goal(session_factory, user_id=owner["user_id"], goal_id=goal_id)
    _seed_goal(session_factory, user_id=other["user_id"], goal_id="goal-foreign")

    with session_factory() as session:
        session.add(
            Document(
                id="doc-unassigned-history",
                owner_user_id=owner["user_id"],
                goal_id=None,
                corpus_type="user_uploaded",
                filename="history.txt",
                object_key="history/doc-unassigned-history.txt",
                mime_type="text/plain",
                parse_status="success",
                sha256="a" * 64,
                trusted_level=1,
            )
        )
        session.commit()

    first = _upload(client, owner, goal_id="goal-a", filename="first.txt")
    second = _upload(client, owner, goal_id="goal-b", filename="second.txt")
    assert first.status_code == second.status_code == 201

    filtered = client.get(
        "/api/documents",
        headers=owner["headers"],
        params={"goal_id": "goal-a"},
    )
    assert [item["id"] for item in filtered.json()["documents"]] == [first.json()["id"]]

    assigned = client.put(
        f"/api/documents/{first.json()['id']}/goal",
        headers=owner["headers"],
        json={"goal_id": "goal-b"},
    )
    repeated = client.put(
        f"/api/documents/{first.json()['id']}/goal",
        headers=owner["headers"],
        json={"goal_id": "goal-b"},
    )
    foreign_goal = client.put(
        f"/api/documents/{first.json()['id']}/goal",
        headers=owner["headers"],
        json={"goal_id": "goal-foreign"},
    )
    foreign_document = client.put(
        f"/api/documents/{first.json()['id']}/goal",
        headers=other["headers"],
        json={"goal_id": "goal-foreign"},
    )
    assigned_history = client.put(
        "/api/documents/doc-unassigned-history/goal",
        headers=owner["headers"],
        json={"goal_id": "goal-a"},
    )

    assert assigned.status_code == repeated.status_code == 200
    assert assigned.json()["goal_id"] == "goal-b"
    assert assigned_history.status_code == 200
    assert assigned_history.json()["goal_id"] == "goal-a"
    assert foreign_goal.status_code == 404
    assert foreign_document.status_code == 404
    goal_b = client.get(
        "/api/documents",
        headers=owner["headers"],
        params={"goal_id": "goal-b"},
    ).json()["documents"]
    assert {item["id"] for item in goal_b} == {first.json()["id"], second.json()["id"]}
