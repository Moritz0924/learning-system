from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.models import AgentRun, ConversationThread, LearningGoal
from tests.conftest import register_user


def _seed_thread(session_factory, *, user_id: str, goal_id: str, thread_id: str) -> None:
    with session_factory() as session:
        session.add(
            LearningGoal(
                id=goal_id,
                user_id=user_id,
                title="Transcript goal",
                target_outcome="Restore persisted tutor history safely",
                weekly_hours_target=4,
            )
        )
        session.flush()
        session.add(
            ConversationThread(
                id=thread_id,
                user_id=user_id,
                goal_id=goal_id,
                title="Transcript thread",
                status="active",
            )
        )
        session.commit()


def _add_run(
    session,
    *,
    run_id: str,
    user_id: str,
    goal_id: str,
    thread_id: str,
    status: str,
    question: str,
    offset: int,
    output: dict | None = None,
) -> None:
    created_at = datetime(2026, 8, 31, 8, 0, 0) + timedelta(minutes=offset)
    session.add(
        AgentRun(
            id=run_id,
            user_id=user_id,
            goal_id=goal_id,
            thread_id=thread_id,
            correlation_id=f"correlation-{run_id}",
            request_hash=str(offset + 1) * 64,
            graph_name="phase2_tutor_graph",
            graph_version="phase2-v1",
            trigger_type="chat",
            input_snapshot={
                "request": {
                    "user_message": question,
                    "api_key": "must-not-leak",
                    "metadata": {"private": "must-not-leak"},
                },
                "prompt": "must-not-leak",
            },
            output_snapshot=output or {},
            node_trace=[{"private": "must-not-leak"}],
            status=status,
            latency_ms=10,
            error_message="must-not-leak" if status == "failed" else None,
            started_at=created_at.replace(tzinfo=timezone.utc),
            completed_at=(created_at + timedelta(seconds=1)).replace(tzinfo=timezone.utc)
            if status in {"success", "failed", "cancelled"}
            else None,
            cancelled_at=created_at.replace(tzinfo=timezone.utc) if status == "cancelled" else None,
            created_at=created_at,
        )
    )


def test_messages_projects_public_success_and_user_only_non_success(client, session_factory) -> None:
    identity = register_user(client, email="transcript-projection@example.com")
    goal_id = "goal-transcript-projection"
    thread_id = "thread-transcript-projection"
    _seed_thread(
        session_factory,
        user_id=identity["user_id"],
        goal_id=goal_id,
        thread_id=thread_id,
    )
    with session_factory() as session:
        _add_run(
            session,
            run_id="run-success",
            user_id=identity["user_id"],
            goal_id=goal_id,
            thread_id=thread_id,
            status="success",
            question="Successful question",
            offset=1,
            output={
                "final_answer": "Persisted public answer",
                "citations": [{"citation_label": "[1]", "source_url": "https://example.test/1"}],
                "grounding_status": "grounded",
                "runtime_metadata": {"private": "must-not-leak"},
            },
        )
        for offset, status in enumerate(("failed", "cancelled", "running"), start=2):
            _add_run(
                session,
                run_id=f"run-{status}",
                user_id=identity["user_id"],
                goal_id=goal_id,
                thread_id=thread_id,
                status=status,
                question=f"{status} question",
                offset=offset,
                output={"final_answer": f"fake {status} assistant"},
            )
        session.commit()

    response = client.get(
        f"/api/tutor/conversations/{thread_id}/messages",
        headers=identity["headers"],
        params={"goal_id": goal_id},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [(item["id"], item["role"], item["content"]) for item in payload["messages"]] == [
        ("run-success:user", "user", "Successful question"),
        ("run-success:assistant", "assistant", "Persisted public answer"),
        ("run-failed:user", "user", "failed question"),
        ("run-cancelled:user", "user", "cancelled question"),
        ("run-running:user", "user", "running question"),
    ]
    assistant = payload["messages"][1]
    assert assistant["citations"] == [
        {"citation_label": "[1]", "source_url": "https://example.test/1"}
    ]
    assert assistant["grounding_status"] == "grounded"
    serialized = response.text
    for private_value in ("must-not-leak", "api_key", "runtime_metadata", "node_trace", "error_message"):
        assert private_value not in serialized


def test_messages_paginate_by_message_and_keep_each_page_ascending(client, session_factory) -> None:
    identity = register_user(client, email="transcript-pagination@example.com")
    goal_id = "goal-transcript-pagination"
    thread_id = "thread-transcript-pagination"
    _seed_thread(
        session_factory,
        user_id=identity["user_id"],
        goal_id=goal_id,
        thread_id=thread_id,
    )
    with session_factory() as session:
        _add_run(
            session,
            run_id="run-page-1",
            user_id=identity["user_id"],
            goal_id=goal_id,
            thread_id=thread_id,
            status="success",
            question="Question one",
            offset=1,
            output={"final_answer": "Answer one", "citations": []},
        )
        _add_run(
            session,
            run_id="run-page-2",
            user_id=identity["user_id"],
            goal_id=goal_id,
            thread_id=thread_id,
            status="failed",
            question="Question two",
            offset=2,
        )
        _add_run(
            session,
            run_id="run-page-3",
            user_id=identity["user_id"],
            goal_id=goal_id,
            thread_id=thread_id,
            status="success",
            question="Question three",
            offset=3,
            output={"final_answer": "Answer three", "citations": []},
        )
        session.commit()

    first = client.get(
        f"/api/tutor/conversations/{thread_id}/messages",
        headers=identity["headers"],
        params={"goal_id": goal_id, "limit": 3},
    )
    assert first.status_code == 200, first.text
    assert [item["id"] for item in first.json()["messages"]] == [
        "run-page-2:user",
        "run-page-3:user",
        "run-page-3:assistant",
    ]
    assert first.json()["next_before"] == "run-page-2:user"

    older = client.get(
        f"/api/tutor/conversations/{thread_id}/messages",
        headers=identity["headers"],
        params={"goal_id": goal_id, "limit": 3, "before": first.json()["next_before"]},
    )
    assert older.status_code == 200, older.text
    assert [item["id"] for item in older.json()["messages"]] == [
        "run-page-1:user",
        "run-page-1:assistant",
    ]
    assert older.json()["next_before"] is None

    before_assistant = client.get(
        f"/api/tutor/conversations/{thread_id}/messages",
        headers=identity["headers"],
        params={"goal_id": goal_id, "limit": 2, "before": "run-page-3:assistant"},
    )
    assert before_assistant.status_code == 200, before_assistant.text
    assert [item["id"] for item in before_assistant.json()["messages"]] == [
        "run-page-2:user",
        "run-page-3:user",
    ]


def test_messages_reject_malformed_and_unknown_cursor_with_400(client, session_factory) -> None:
    identity = register_user(client, email="transcript-invalid-cursor@example.com")
    goal_id = "goal-transcript-invalid-cursor"
    thread_id = "thread-transcript-invalid-cursor"
    _seed_thread(
        session_factory,
        user_id=identity["user_id"],
        goal_id=goal_id,
        thread_id=thread_id,
    )

    for before in ("bad", "run-id:system", "run-missing:user"):
        response = client.get(
            f"/api/tutor/conversations/{thread_id}/messages",
            headers=identity["headers"],
            params={"goal_id": goal_id, "before": before},
        )
        assert response.status_code == 400, (before, response.text)


def test_messages_hide_thread_goal_user_and_cursor_scope_mismatches(client, session_factory) -> None:
    owner = register_user(client, email="transcript-owner@example.com")
    other = register_user(client, email="transcript-other@example.com")
    owner_goal = "goal-transcript-owner"
    owner_thread = "thread-transcript-owner"
    other_goal = "goal-transcript-other"
    other_thread = "thread-transcript-other"
    _seed_thread(
        session_factory,
        user_id=owner["user_id"],
        goal_id=owner_goal,
        thread_id=owner_thread,
    )
    _seed_thread(
        session_factory,
        user_id=other["user_id"],
        goal_id=other_goal,
        thread_id=other_thread,
    )
    with session_factory() as session:
        _add_run(
            session,
            run_id="run-foreign-cursor",
            user_id=other["user_id"],
            goal_id=other_goal,
            thread_id=other_thread,
            status="failed",
            question="Foreign question",
            offset=1,
        )
        session.commit()

    wrong_goal = client.get(
        f"/api/tutor/conversations/{owner_thread}/messages",
        headers=owner["headers"],
        params={"goal_id": other_goal},
    )
    assert wrong_goal.status_code == 404

    wrong_user = client.get(
        f"/api/tutor/conversations/{other_thread}/messages",
        headers=owner["headers"],
        params={"goal_id": other_goal},
    )
    assert wrong_user.status_code == 404

    foreign_cursor = client.get(
        f"/api/tutor/conversations/{owner_thread}/messages",
        headers=owner["headers"],
        params={"goal_id": owner_goal, "before": "run-foreign-cursor:user"},
    )
    assert foreign_cursor.status_code == 404
