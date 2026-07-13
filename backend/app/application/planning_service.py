from __future__ import annotations

import json
from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import TutorRunRequest
from backend.app.application.engine import _run_engine
from backend.app.application.learning_activity_service import (
    _load_goal_for_user,
    _record_learning_event,
    _refresh_activity_state,
)
from backend.app.application.serialization import (
    _json_dict,
    _plan_adjustment_model_to_dict,
    _plan_adjustment_record_to_dict,
    _task_to_dict,
)
from backend.app.core.exceptions import PlanApplicationConflict
from backend.app.models import (
    KnowledgeNode,
    LearningGoal,
    LearningPlan,
    LearningStateSnapshot,
    PlanAdjustmentRecord,
    PlanTask,
)


def request_replan(
    session: Session,
    *,
    user_id: str,
    goal_id: str,
    message: str,
) -> dict:
    _load_goal_for_user(session, user_id=user_id, goal_id=goal_id)
    result = _run_engine(
        session,
        TutorRunRequest(
            trigger_type="manual_replan",
            user_id=user_id,
            goal_id=goal_id,
            thread_id="manual-replan",
            user_message=message,
        ),
    )
    session.commit()
    if result.plan_adjustment is None:
        raise RuntimeError("phase2 engine did not return a plan adjustment")
    return _plan_adjustment_model_to_dict(result.plan_adjustment)

def apply_plan_adjustment(
    session: Session,
    *,
    adjustment_id: str,
    user_id: str,
    goal_id: str,
) -> dict:
    record = session.get(PlanAdjustmentRecord, adjustment_id)
    if record is None or record.user_id != user_id or record.goal_id != goal_id:
        raise LookupError(f"plan adjustment {adjustment_id} not found")
    if record.status != "proposed":
        raise PlanApplicationConflict(f"plan adjustment {adjustment_id} is not proposed")
    patch = _json_dict(record.plan_patch)
    if record.decision == "keep" or patch.get("no_change"):
        raise PlanApplicationConflict("no applicable plan patch for keep adjustment")

    goal = session.scalar(
        select(LearningGoal)
        .where(
            LearningGoal.id == goal_id,
            LearningGoal.user_id == user_id,
        )
        .with_for_update()
    )
    if goal is None:
        raise LookupError(f"learning goal {goal_id} not found")

    claim = session.execute(
        update(PlanAdjustmentRecord)
        .where(
            PlanAdjustmentRecord.id == adjustment_id,
            PlanAdjustmentRecord.user_id == user_id,
            PlanAdjustmentRecord.goal_id == goal_id,
            PlanAdjustmentRecord.status == "proposed",
        )
        .values(status="applying")
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        session.rollback()
        raise PlanApplicationConflict(f"plan adjustment {adjustment_id} is no longer proposed")
    record.status = "applying"

    snapshot = session.scalar(
        select(LearningStateSnapshot)
        .where(
            LearningStateSnapshot.user_id == user_id,
            LearningStateSnapshot.goal_id == goal_id,
        )
        .with_for_update()
    )
    previous_plan_id = record.previous_plan_id or (snapshot.active_plan_id if snapshot else None)
    if snapshot is not None and previous_plan_id != snapshot.active_plan_id:
        session.rollback()
        raise PlanApplicationConflict("active plan has changed since this adjustment was proposed")
    previous_plan = session.get(LearningPlan, previous_plan_id) if previous_plan_id else None
    if previous_plan is None:
        raise LookupError("previous learning plan not found")
    if previous_plan.status != "active":
        session.rollback()
        raise PlanApplicationConflict("active plan has changed since this adjustment was proposed")

    previous_plan.status = "replaced"
    session.flush()
    created_tasks = _create_applied_plan_tasks(
        session,
        previous_plan=previous_plan,
        adjustment=record,
        snapshot=snapshot,
    )
    new_plan = created_tasks["plan"]
    task_payloads = created_tasks["tasks"]

    record.status = "applied"
    record.new_plan_id = new_plan.id
    record.after_snapshot = {
        **_json_dict(record.after_snapshot),
        "active_plan": {"id": new_plan.id, "version": new_plan.version},
        "created_task_ids": [task["id"] for task in task_payloads],
    }
    if snapshot is not None:
        snapshot.active_plan_id = new_plan.id
        snapshot.active_plan_version = new_plan.version
        snapshot.latest_plan_adjustment_id = record.id
        current_state = dict(snapshot.current_state or {})
        current_state["latest_plan_adjustment"] = _plan_adjustment_record_to_dict(record)
        snapshot.current_state = current_state
        generated_from = dict(snapshot.generated_from or {})
        generated_from["latest_plan_adjustment_id"] = record.id
        generated_from["active_plan_id"] = new_plan.id
        snapshot.generated_from = generated_from
    _record_learning_event(
        session,
        user_id=user_id,
        goal_id=goal_id,
        task_id=None,
        session_id=None,
        event_type="plan_adjustment_applied",
        source="plans_api",
        event_payload={
            "adjustment_id": record.id,
            "previous_plan_id": previous_plan.id,
            "new_plan_id": new_plan.id,
            "decision": record.decision,
        },
    )
    _refresh_activity_state(session, user_id=user_id, goal_id=goal_id)
    session.commit()
    payload = _plan_adjustment_record_to_dict(record)
    payload["active_plan"] = {"id": new_plan.id, "version": new_plan.version}
    payload["created_tasks"] = task_payloads
    return payload

def _create_applied_plan_tasks(
    session: Session,
    *,
    previous_plan: LearningPlan,
    adjustment: PlanAdjustmentRecord,
    snapshot: LearningStateSnapshot | None,
) -> dict:
    patch = _json_dict(adjustment.plan_patch)
    previous_tasks = list(
        session.scalars(
            select(PlanTask)
            .where(PlanTask.plan_id == previous_plan.id)
            .order_by(PlanTask.scheduled_day, PlanTask.priority, PlanTask.id)
        )
    )
    open_tasks = [task for task in previous_tasks if (task.status or "").lower() not in {"completed", "done"}]
    new_plan = LearningPlan(
        id=f"plan-{uuid4()}",
        user_id=previous_plan.user_id,
        goal_id=previous_plan.goal_id,
        curriculum_id=previous_plan.curriculum_id,
        version=_next_plan_version(session, previous_plan.user_id, previous_plan.goal_id),
        status="active",
        generated_by="planner",
        rationale_json={
            "source": "plan_adjustment",
            "adjustment_id": adjustment.id,
            "decision": adjustment.decision,
        },
        valid_from=date.today(),
        valid_to=previous_plan.valid_to,
        plan_json={
            **(previous_plan.plan_json or {}),
            "applied_adjustment_id": adjustment.id,
            "previous_plan_id": previous_plan.id,
        },
    )
    session.add(new_plan)
    session.flush()

    created: list[PlanTask] = []
    day_offset = 0
    if adjustment.decision == "remediate":
        review_count = int(patch.get("review_task_count", 2))
        review_nodes = _review_nodes_for_adjustment(session, adjustment=adjustment, snapshot=snapshot, fallback_tasks=open_tasks)
        for index, node in enumerate(review_nodes[:review_count], start=1):
            created.append(
                _add_plan_task(
                    session,
                    plan=new_plan,
                    knowledge_node_id=node["id"],
                    knowledge_node_code=node["code"],
                    title=f"Review {node['code']}",
                    task_type="review",
                    objective="Review weak knowledge area before continuing.",
                    scheduled_day=index,
                    estimated_minutes=30,
                    priority=0,
                    payload={"source": "plan_adjustment", "adjustment_id": adjustment.id},
                )
            )
        day_offset = len(created)

    multiplier = float(patch.get("load_multiplier", 1.0)) if adjustment.decision == "reduce" else 1.0
    for task in open_tasks:
        created.append(
            _clone_plan_task(
                session,
                source=task,
                plan=new_plan,
                scheduled_day=task.scheduled_day + day_offset,
                estimated_minutes=max(10, int(round(task.estimated_minutes * multiplier))),
                adjustment_id=adjustment.id,
            )
        )

    if adjustment.decision == "advance":
        next_node = _next_uncovered_node(session, previous_plan=previous_plan, tasks=previous_tasks)
        if next_node is not None:
            created.append(
                _add_plan_task(
                    session,
                    plan=new_plan,
                    knowledge_node_id=next_node.id,
                    knowledge_node_code=next_node.code,
                    title=f"Practice {next_node.code}",
                    task_type="practice",
                    objective=f"Apply {next_node.code.replace('_', ' ')} in a short practice task.",
                    scheduled_day=(max([task.scheduled_day for task in created], default=0) + 1),
                    estimated_minutes=45,
                    priority=2,
                    payload={"source": "plan_adjustment", "adjustment_id": adjustment.id},
                )
            )

    session.flush()
    return {"plan": new_plan, "tasks": [_task_to_dict(task) for task in created]}

def _add_plan_task(
    session: Session,
    *,
    plan: LearningPlan,
    knowledge_node_id: str,
    knowledge_node_code: str,
    title: str,
    task_type: str,
    objective: str,
    scheduled_day: int,
    estimated_minutes: int,
    priority: int,
    payload: dict,
) -> PlanTask:
    task = PlanTask(
        id=f"task-{uuid4()}",
        plan_id=plan.id,
        user_id=plan.user_id,
        goal_id=plan.goal_id,
        knowledge_node_id=knowledge_node_id,
        knowledge_node_code=knowledge_node_code,
        title=title,
        task_type=task_type,
        objective=objective,
        scheduled_date=date.today() + timedelta(days=max(0, scheduled_day - 1)),
        scheduled_day=scheduled_day,
        estimated_minutes=estimated_minutes,
        priority=priority,
        status="pending",
        payload=payload,
        origin="planner",
    )
    session.add(task)
    return task

def _clone_plan_task(
    session: Session,
    *,
    source: PlanTask,
    plan: LearningPlan,
    scheduled_day: int,
    estimated_minutes: int,
    adjustment_id: str,
) -> PlanTask:
    payload = dict(source.payload or {})
    payload.update({"source": "plan_adjustment", "adjustment_id": adjustment_id, "previous_task_id": source.id})
    return _add_plan_task(
        session,
        plan=plan,
        knowledge_node_id=source.knowledge_node_id,
        knowledge_node_code=source.knowledge_node_code,
        title=source.title,
        task_type=source.task_type,
        objective=source.objective,
        scheduled_day=scheduled_day,
        estimated_minutes=estimated_minutes,
        priority=source.priority,
        payload=payload,
    )

def _review_nodes_for_adjustment(
    session: Session,
    *,
    adjustment: PlanAdjustmentRecord,
    snapshot: LearningStateSnapshot | None,
    fallback_tasks: list[PlanTask],
) -> list[dict]:
    evidence = _json_dict(adjustment.evidence_json)
    signals = _json_dict(evidence.get("observer_signals", {}))
    candidates = list(signals.get("low_mastery_nodes") or [])
    if snapshot is not None:
        candidates.extend((snapshot.current_state or {}).get("review_queue", []))
    seen: set[str] = set()
    nodes: list[dict] = []
    for item in candidates:
        node_id = item.get("knowledge_node_id")
        if not node_id or node_id in seen:
            continue
        node = session.get(KnowledgeNode, node_id)
        if node is None:
            continue
        seen.add(node_id)
        nodes.append({"id": node.id, "code": node.code})
    for task in fallback_tasks:
        if task.knowledge_node_id not in seen:
            seen.add(task.knowledge_node_id)
            nodes.append({"id": task.knowledge_node_id, "code": task.knowledge_node_code})
    return nodes

def _next_uncovered_node(session: Session, *, previous_plan: LearningPlan, tasks: list[PlanTask]) -> KnowledgeNode | None:
    covered = {task.knowledge_node_id for task in tasks}
    nodes = list(
        session.scalars(
            select(KnowledgeNode)
            .where(KnowledgeNode.curriculum_id == previous_plan.curriculum_id)
            .order_by(KnowledgeNode.sequence)
        )
    )
    for node in nodes:
        if node.id not in covered:
            return node
    return nodes[-1] if nodes else None

def _next_plan_version(session: Session, user_id: str, goal_id: str) -> int:
    versions = session.scalars(
        select(LearningPlan.version).where(LearningPlan.user_id == user_id, LearningPlan.goal_id == goal_id)
    ).all()
    return max(versions, default=0) + 1
