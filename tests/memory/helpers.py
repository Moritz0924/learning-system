from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.app.domain.memory.contracts import CreateMemoryCommand
from backend.app.models import LearningGoal, User


FIXED_NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def create_test_user() -> str:
    return "test-user"


def create_test_goal() -> str:
    return "test-goal"


def add_memory_scope(
    session: Session,
    *,
    user_id: str | None = None,
    goal_id: str | None = None,
) -> tuple[str, str]:
    resolved_user_id = user_id or create_test_user()
    resolved_goal_id = goal_id or create_test_goal()
    if session.get(User, resolved_user_id) is None:
        session.add(
            User(
                id=resolved_user_id,
                email=f"{resolved_user_id}@example.com",
                normalized_email=f"{resolved_user_id}@example.com",
                display_name="Memory Test User",
            )
        )
        session.flush()
    session.add(
        LearningGoal(
            id=resolved_goal_id,
            user_id=resolved_user_id,
            title="Learn Python",
            target_outcome="Build reliable Python applications",
            weekly_hours_target=5,
        )
    )
    session.flush()
    return resolved_user_id, resolved_goal_id


def preference_command(**overrides: Any) -> CreateMemoryCommand:
    data: dict[str, Any] = {
        "user_id": create_test_user(),
        "memory_type": "learning_preference",
        "content": {"preference_key": "explanation_style", "preference_value": "examples"},
        "source_kind": "explicit_user",
        "idempotency_key": "preference:explanation-style:v1",
    }
    data.update(overrides)
    return CreateMemoryCommand(**data)


def mastery_command(**overrides: Any) -> CreateMemoryCommand:
    data: dict[str, Any] = {
        "user_id": create_test_user(),
        "goal_id": create_test_goal(),
        "memory_type": "mastery_summary",
        "content": {
            "knowledge_node_id": "python-basics",
            "score": 86.5,
            "confidence": 0.8,
            "evidence_count": 3,
            "calculation_version": "mastery-v1",
        },
        "source_kind": "mastery_record",
        "source_ref_id": "mastery-record-1",
        "expires_at": FIXED_NOW + timedelta(days=7),
        "idempotency_key": "mastery:python-basics:v1",
    }
    data.update(overrides)
    return CreateMemoryCommand(**data)


def milestone_command(**overrides: Any) -> CreateMemoryCommand:
    data: dict[str, Any] = {
        "user_id": create_test_user(),
        "goal_id": create_test_goal(),
        "memory_type": "learning_milestone",
        "content": {
            "milestone_code": "python-basics-complete",
            "title": "Completed Python basics",
            "achieved_at": FIXED_NOW,
            "evidence_refs": ["assessment-1"],
        },
        "source_kind": "assessment",
        "source_ref_id": "assessment-1",
        "idempotency_key": "milestone:python-basics:v1",
    }
    data.update(overrides)
    return CreateMemoryCommand(**data)
