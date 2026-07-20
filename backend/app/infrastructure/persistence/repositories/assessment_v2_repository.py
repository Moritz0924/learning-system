from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.application.assessment_generation_service import AssessmentGenerationOutcome
from backend.app.domain.assessment.contracts import AssessmentGenerationBundleV2
from backend.app.models import Assessment, AssessmentAttempt, AssessmentItem, LearningStateSnapshot


@dataclass(frozen=True)
class SubmissionClaim:
    attempt_id: str
    claim_token: str
    assessment_id: str


class AssessmentV2Repository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_generation(self, *, user_id: str, request_id: str) -> Assessment | None:
        return self.session.scalar(
            select(Assessment).where(Assessment.user_id == user_id, Assessment.generation_request_id == request_id)
        )

    def save_generation(
        self,
        *,
        user_id: str,
        goal_id: str,
        assessment_type: str,
        request_id: str,
        input_hash: str,
        bundle: AssessmentGenerationBundleV2,
        outcome: AssessmentGenerationOutcome,
        knowledge_node_ids: list[str],
    ) -> Assessment:
        snapshot = self.session.scalar(
            select(LearningStateSnapshot).where(LearningStateSnapshot.user_id == user_id, LearningStateSnapshot.goal_id == goal_id)
        )
        assessment = Assessment(
            id=f"assessment-{uuid4()}",
            user_id=user_id,
            goal_id=goal_id,
            plan_id=snapshot.active_plan_id if snapshot else None,
            assessment_type=assessment_type,
            scope={"knowledge_node_ids": knowledge_node_ids},
            status="active",
            rubric_version="assessment-rubric-v2",
            generation_request_id=request_id,
            generation_input_hash=input_hash,
            schema_version="assessment-v2",
            generation_mode=outcome.mode,
            generator_version=bundle.generator_version,
            generator_model=outcome.model,
            generation_metadata=_safe_metadata(outcome.metadata),
        )
        self.session.add(assessment)
        for item in bundle.items:
            item_id = f"item-{uuid4()}"
            self.session.add(
                AssessmentItem(
                    id=item_id,
                    assessment_id=assessment.id,
                    knowledge_node_id=item.knowledge_node_id,
                    question_type=item.question_type,
                    prompt=item.prompt,
                    options_json={
                        "options": [
                            {"option_id": option.option_key, "label": option.label}
                            for option in item.options
                        ],
                        "correct_option_id": item.reference_answer if item.question_type == "choice" else None,
                    },
                    reference_answer=item.reference_answer,
                    rubric_json={
                        "schema_version": "assessment-rubric-v2",
                        "target_skill": item.target_skill,
                        "criteria": [criterion.model_dump() for criterion in item.rubric],
                    },
                    difficulty=item.difficulty,
                    source_chunk_ids=item.source_chunk_ids,
                )
            )
        self.session.flush()
        return assessment

    def load_items(self, assessment_id: str) -> list[AssessmentItem]:
        return list(
            self.session.scalars(select(AssessmentItem).where(AssessmentItem.assessment_id == assessment_id).order_by(AssessmentItem.id))
        )

    def lock_assessment(self, *, assessment_id: str, user_id: str) -> Assessment | None:
        return self.session.scalar(
            select(Assessment)
            .where(Assessment.id == assessment_id, Assessment.user_id == user_id)
            .with_for_update()
        )

    def lock_attempt_by_request(self, *, assessment_id: str, user_id: str, request_id: str) -> AssessmentAttempt | None:
        return self.session.scalar(
            select(AssessmentAttempt)
            .where(
                AssessmentAttempt.assessment_id == assessment_id,
                AssessmentAttempt.user_id == user_id,
                AssessmentAttempt.request_id == request_id,
            )
            .with_for_update()
        )

    def active_attempt(self, *, assessment_id: str) -> AssessmentAttempt | None:
        return self.session.scalar(
            select(AssessmentAttempt)
            .where(AssessmentAttempt.assessment_id == assessment_id, AssessmentAttempt.status == "grading")
            .with_for_update()
        )

    def claim_assessment_if_active(self, *, assessment_id: str, user_id: str) -> bool:
        result = self.session.execute(
            update(Assessment)
            .where(
                Assessment.id == assessment_id,
                Assessment.user_id == user_id,
                Assessment.status == "active",
            )
            .values(status="grading")
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def create_claim(self, *, assessment: Assessment, user_id: str, request_id: str, answer_hash: str, answers: dict[str, str], lease_seconds: int) -> SubmissionClaim:
        token = str(uuid4())
        attempt = AssessmentAttempt(
            id=f"attempt-{uuid4()}",
            assessment_id=assessment.id,
            user_id=user_id,
            score=None,
            feedback="",
            status="grading",
            request_id=request_id,
            answer_payload_hash=answer_hash,
            submitted_answers_json=answers,
            grader_mode="pending",
            grader_version="assessment-grader-v2",
            grading_metadata={},
            claim_token=token,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=lease_seconds),
            attempt_count=1,
        )
        assessment.status = "grading"
        self.session.add(attempt)
        self.session.flush()
        return SubmissionClaim(attempt.id, token, assessment.id)

    def reclaim_claim(self, *, attempt: AssessmentAttempt, assessment: Assessment, lease_seconds: int) -> SubmissionClaim:
        token = str(uuid4())
        attempt.status = "grading"
        attempt.claim_token = token
        attempt.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        attempt.attempt_count += 1
        attempt.error_code = None
        assessment.status = "grading"
        self.session.flush()
        return SubmissionClaim(attempt.id, token, assessment.id)


def _safe_metadata(metadata: dict[str, object]) -> dict[str, object]:
    permitted = {"mode", "model", "prompt_version", "latency_ms", "retry_count", "repair_count", "error_code", "input_hash", "output_hash"}
    return {key: value for key, value in metadata.items() if key in permitted}
