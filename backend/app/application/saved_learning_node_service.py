from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    LearningGoal,
    LearningStateSnapshot,
    PlanTask,
    SavedLearningNode,
)


def _require_goal(session: Session, *, user_id: str, goal_id: str) -> None:
    if session.scalar(
        select(LearningGoal.id).where(
            LearningGoal.id == goal_id,
            LearningGoal.user_id == user_id,
        )
    ) is None:
        raise LookupError("learning goal not found")


def list_saved_learning_nodes(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
) -> list[str]:
    _require_goal(session, user_id=user_id, goal_id=goal_id)
    return list(
        session.scalars(
            select(SavedLearningNode.knowledge_node_id)
            .where(
                SavedLearningNode.user_id == user_id,
                SavedLearningNode.goal_id == goal_id,
            )
            .order_by(SavedLearningNode.created_at, SavedLearningNode.knowledge_node_id)
        )
    )


def save_learning_node(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    knowledge_node_id: str,
) -> None:
    _require_goal(session, user_id=user_id, goal_id=goal_id)
    snapshot = session.scalar(
        select(LearningStateSnapshot).where(
            LearningStateSnapshot.user_id == user_id,
            LearningStateSnapshot.goal_id == goal_id,
        )
    )
    in_active_plan = snapshot is not None and session.scalar(
        select(PlanTask.id).where(
            PlanTask.plan_id == snapshot.active_plan_id,
            PlanTask.user_id == user_id,
            PlanTask.goal_id == goal_id,
            PlanTask.knowledge_node_id == knowledge_node_id,
        )
    ) is not None
    if not in_active_plan:
        raise LookupError("learning node not found")
    key = (user_id, goal_id, knowledge_node_id)
    if session.get(SavedLearningNode, key) is None:
        session.add(
            SavedLearningNode(
                user_id=user_id,
                goal_id=goal_id,
                knowledge_node_id=knowledge_node_id,
            )
        )
        session.commit()


def delete_saved_learning_node(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    knowledge_node_id: str,
) -> None:
    _require_goal(session, user_id=user_id, goal_id=goal_id)
    record = session.get(SavedLearningNode, (user_id, goal_id, knowledge_node_id))
    if record is not None:
        session.delete(record)
        session.commit()
