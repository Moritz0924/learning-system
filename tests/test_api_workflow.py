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
