from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from adaptive_tutor.tutor.t3_contracts import canonical_json_hash
from backend.app.core.exceptions import FeedbackIdempotencyConflict
from backend.app.models import AgentRun, UserFeedback


def build_eval_candidate(
    *,
    feedback_id: str,
    run_id: str,
    helpful: bool,
    reason_code: str,
    sanitized_input: str,
    review_approved: bool,
) -> dict | None:
    if not review_approved:
        return None
    return {
        "case_id": f"feedback-case-{feedback_id}",
        "run_id": run_id,
        "input": sanitized_input,
        "helpful": helpful,
        "reason_code": reason_code,
        "dataset_version": "feedback-candidate-v1",
    }


def submit_tutor_feedback(
    session: Session,
    *,
    user_id: str,
    run_id: str,
    helpful: bool,
    citation_correct: bool | None,
    difficulty_fit: bool | None,
    reason_code: str,
    optional_comment: str | None,
) -> dict:
    run = session.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id))
    if run is None:
        raise LookupError(f"run {run_id} not found")
    payload = {
        "helpful": helpful,
        "citation_correct": citation_correct,
        "difficulty_fit": difficulty_fit,
        "reason_code": reason_code,
        "optional_comment": optional_comment,
    }
    payload_hash = canonical_json_hash(payload)
    existing = session.scalar(
        select(UserFeedback).where(UserFeedback.user_id == user_id, UserFeedback.run_id == run_id)
    )
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise FeedbackIdempotencyConflict("feedback payload conflicts with existing feedback")
        return {"feedback_id": existing.id, "run_id": existing.run_id, "replayed": True}
    feedback = UserFeedback(
        id=f"feedback-{uuid4()}",
        user_id=user_id,
        run_id=run_id,
        payload_hash=payload_hash,
        sanitized_case_json={},
        **payload,
    )
    session.add(feedback)
    session.commit()
    return {"feedback_id": feedback.id, "run_id": feedback.run_id, "replayed": False}
