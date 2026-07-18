from __future__ import annotations

import json

import pytest

from adaptive_tutor.phase2.schemas import AssessmentDraft, AssessmentItem
from backend.app.application.serialization import assessment_draft_to_public
from tests.assessment.helpers import create_assessment, create_learning_goal


SECRET_FIELDS = {
    "correct_option_id",
    "expected_concepts",
    "is_correct",
    "provider_raw_response",
    "reference_answer",
    "rubric_json",
    "source_chunk_ids",
}


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            nested_key
            for nested_value in value.values()
            for nested_key in _all_keys(nested_value)
        }
    if isinstance(value, list):
        return {
            nested_key
            for nested_value in value
            for nested_key in _all_keys(nested_value)
        }
    return set()


def test_daily_assessment_response_omits_grading_secrets(client) -> None:
    goal = create_learning_goal(client, identity="daily-public-contract")

    response = create_assessment(client, goal)

    assert response.status_code == 201, response.text
    payload = response.json()
    assert SECRET_FIELDS.isdisjoint(_all_keys(payload))
    assert set(payload) == {"assessment_id", "assessment_type", "status", "scope", "items"}
    assert set(payload["items"][0]) == {
        "item_id",
        "knowledge_node_id",
        "question_type",
        "prompt",
        "options",
        "difficulty",
    }


def test_phase_assessment_response_uses_public_contract_and_keeps_phase_fields(client) -> None:
    goal = create_learning_goal(client, identity="phase-public-contract")

    response = client.post(
        "/api/assessments/phase",
        headers=goal["headers"],
        json={
            "goal_id": goal["goal_id"],
            "thread_id": "phase-public-thread",
            "phase_code": "phase-secure-assessment-v1",
            "knowledge_node_ids": [goal["knowledge_node_id"]],
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert SECRET_FIELDS.isdisjoint(_all_keys(payload))
    assert payload["phase_assessment_state_id"]
    assert payload["phase_code"] == "phase-secure-assessment-v1"
    assert set(payload["items"][0]) == {
        "item_id",
        "knowledge_node_id",
        "question_type",
        "prompt",
        "options",
        "difficulty",
    }


def test_openapi_declares_only_public_assessment_response_models(client) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    daily_schema = paths["/api/assessments"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"]
    phase_schema = paths["/api/assessments/phase"]["post"]["responses"]["201"][
        "content"
    ]["application/json"]["schema"]

    assert daily_schema["$ref"].endswith("/AssessmentPublicResponse")
    assert phase_schema["$ref"].endswith("/PhaseAssessmentPublicResponse")
    public_components = {
        key: value
        for key, value in schema["components"]["schemas"].items()
        if key in {
            "AssessmentItemPublic",
            "AssessmentOptionPublic",
            "AssessmentPublicResponse",
            "PhaseAssessmentPublicResponse",
        }
    }
    assert SECRET_FIELDS.isdisjoint(_all_keys(public_components))
    assert SECRET_FIELDS.isdisjoint(_all_keys(schema))
    serialized_schema = json.dumps(schema, sort_keys=True)
    for secret in SECRET_FIELDS:
        assert secret not in serialized_schema


def test_public_serializer_allowlists_option_fields() -> None:
    draft = AssessmentDraft(
        assessment_id="assessment-options",
        assessment_type="daily",
        scope={
            "knowledge_node_ids": ["node-options"],
            "source_chunk_body": "private retrieval text",
        },
        items=[
            AssessmentItem(
                item_id="item-options",
                knowledge_node_id="node-options",
                question_type="choice",
                prompt="Choose one option.",
                options_json={
                    "options": [
                        {
                            "option_id": "option-a",
                            "label": "Public label",
                            "is_correct": True,
                            "score": 100,
                        }
                    ],
                    "correct_option_id": "option-a",
                },
                reference_answer="option-a",
                rubric_json={"rule": "secret"},
                difficulty=2,
                source_chunk_ids=["chunk-secret"],
            )
        ],
    )

    payload = assessment_draft_to_public(draft).model_dump()

    assert payload["scope"] == {"knowledge_node_ids": ["node-options"]}
    assert payload["items"][0]["options"] == [
        {"option_id": "option-a", "label": "Public label"}
    ]
    assert "private retrieval text" not in str(payload)


def test_public_serializer_rejects_malformed_internal_options() -> None:
    draft = AssessmentDraft(
        assessment_id="assessment-malformed-options",
        assessment_type="daily",
        scope={"knowledge_node_ids": ["node-options"]},
        items=[
            AssessmentItem(
                item_id="item-malformed-options",
                knowledge_node_id="node-options",
                question_type="choice",
                prompt="Choose one option.",
                options_json={"options": [{"option_id": "option-a"}]},
                reference_answer="option-a",
                difficulty=2,
            )
        ],
    )

    with pytest.raises(ValueError, match="assessment option"):
        assessment_draft_to_public(draft)
