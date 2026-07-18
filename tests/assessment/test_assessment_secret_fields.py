from __future__ import annotations

from backend.app.infrastructure.persistence.repositories.assessment_repository import (
    SQLAlchemyAssessmentRepository,
)
from backend.app.models import Assessment, AssessmentItem
from tests.assessment.helpers import create_assessment, create_learning_goal


def test_internal_repository_retains_grading_secrets(client, session_factory) -> None:
    goal = create_learning_goal(client, identity="internal-assessment-contract")
    response = create_assessment(client, goal)
    assert response.status_code == 201, response.text

    with session_factory() as session:
        assessment = session.get(Assessment, response.json()["assessment_id"])
        item = session.get(AssessmentItem, response.json()["items"][0]["item_id"])
        item.reference_answer = "private reference answer"
        item.rubric_json = {"rule_version": "private-rubric-v1"}
        item.source_chunk_ids = ["private-source-chunk"]
        session.commit()

    with session_factory() as session:
        draft = SQLAlchemyAssessmentRepository(
            session,
            goal["user_id"],
            goal["goal_id"],
        ).get_assessment_draft(response.json()["assessment_id"])

    internal_item = next(
        item
        for item in draft.items
        if item.item_id == response.json()["items"][0]["item_id"]
    )
    assert internal_item.reference_answer == "private reference answer"
    assert internal_item.rubric_json == {"rule_version": "private-rubric-v1"}
    assert internal_item.source_chunk_ids == ["private-source-chunk"]
