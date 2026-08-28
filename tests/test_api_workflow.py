import json

from sqlalchemy import select

from backend.app.models import KnowledgeNode, LearningStateSnapshot
from tests.conftest import register_user


def test_stage1_api_workflow_creates_goal_diagnosis_state_and_today_tasks(client):
    identity = register_user(client, email="api-user@example.com", display_name="API Learner")
    initialized = client.post(
        "/api/onboarding/initialize",
        headers=identity["headers"],
        json={
            "title": "Learn AI application development",
            "target_outcome": "Build and deploy a RAG demo",
            "deadline": "2026-08-15",
            "weekly_hours_target": 10,
            "learning_preferences": {"style": "concept_then_code"},
            "self_assessment": {
                "python_level": 1,
                "api_level": 0,
                "llm_level": 0,
                "rag_level": 0,
                "langgraph_level": 0,
            },
            "submitted_answers": {
                "questions": [
                    {"node_code": "python_foundations", "is_correct": False}
                ]
            },
        },
    )
    assert initialized.status_code == 201
    goal_payload = initialized.json()["goal"]
    diagnosis_payload = initialized.json()["diagnosis"]
    assert diagnosis_payload["entry_node_code"] == "python_foundations"
    assert diagnosis_payload["active_plan_version"] == 1

    headers = identity["headers"]
    state_response = client.get(
        f"/api/state/current?goal_id={goal_payload['goal_id']}",
        headers=headers,
    )
    tasks_response = client.get(
        f"/api/tasks/today?goal_id={goal_payload['goal_id']}",
        headers=headers,
    )

    assert state_response.status_code == 200
    state_payload = state_response.json()
    assert state_payload["goal"]["id"] == goal_payload["goal_id"]
    assert state_payload["active_plan"]["version"] == 1
    assert state_payload["baseline_diagnostic"]["id"] == diagnosis_payload["baseline_diagnostic_id"]
    assert state_payload["generated_from"]["baseline_diagnostic_id"] == diagnosis_payload["baseline_diagnostic_id"]
    assert state_payload["today_tasks"]

    assert tasks_response.status_code == 200
    task_payload = tasks_response.json()
    assert task_payload["goal_id"] == goal_payload["goal_id"]
    assert len(task_payload["tasks"]) >= 1
    assert task_payload["tasks"][0]["knowledge_node_code"] == "python_foundations"


def test_state_mastery_projection_hides_internal_node_identifiers(client, session_factory):
    """Replacing display labels with snapshot keys must fail this public-state contract."""
    identity = register_user(client, email="mastery-projection@example.com")
    initialized = client.post(
        "/api/onboarding/initialize",
        headers=identity["headers"],
        json={
            "title": "Learn safe state projections",
            "target_outcome": "Display learner mastery without internal identifiers",
            "deadline": "2026-09-01",
            "weekly_hours_target": 8,
            "learning_preferences": {"style": "examples_first"},
            "self_assessment": {"python_level": 2},
            "submitted_answers": {"questions": [{"node_code": "python_foundations", "is_correct": True}]},
        },
    )
    goal_id = initialized.json()["goal"]["goal_id"]
    with session_factory() as session:
        snapshot = session.scalar(
            select(LearningStateSnapshot).where(LearningStateSnapshot.goal_id == goal_id)
        )
        node = session.scalar(select(KnowledgeNode).where(KnowledgeNode.code == "python_foundations"))
        assert snapshot is not None and node is not None
        snapshot.mastery_summary = {
            "user_0123456789abcdef_internal_node": {
                "knowledge_node_id": node.id,
                "node_code": "user_0123456789abcdef_internal_node",
                "score": 73,
                "confidence": 0.8,
                "evidence_count": 2,
                "source_breakdown": {"diagnostic": {"question_ids": ["private-question"]}},
            }
        }
        session.commit()

    response = client.get(f"/api/state/current?goal_id={goal_id}", headers=identity["headers"])

    assert response.status_code == 200, response.text
    assert response.json()["mastery_summary"] == [
        {"label": node.title, "score": 73, "confidence": 0.8, "evidence_count": 2}
    ]
    rendered = json.dumps(response.json()["mastery_summary"])
    assert "user_0123456789abcdef_internal_node" not in rendered
    assert "private-question" not in rendered
