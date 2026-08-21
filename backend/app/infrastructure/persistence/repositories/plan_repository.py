from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.orm import Session

from adaptive_tutor.phase2.schemas import PlanAdjustment
from backend.app.models import PlanAdjustmentRecord


@dataclass
class SQLAlchemyPlanRepository:
    session: Session

    def save_plan_adjustment(self, adjustment: PlanAdjustment) -> PlanAdjustment:
        record_id = adjustment.adjustment_id or f"adjustment-{uuid4()}"
        record = PlanAdjustmentRecord(
            id=record_id,
            user_id=adjustment.user_id or "",
            goal_id=adjustment.goal_id or "",
            previous_plan_id=adjustment.previous_plan_id,
            new_plan_id=adjustment.new_plan_id,
            trigger_type=adjustment.trigger_type,
            decision=adjustment.decision,
            evidence_json=adjustment.evidence_json,
            before_snapshot=adjustment.before_snapshot,
            after_snapshot=adjustment.after_snapshot,
            plan_patch=adjustment.plan_patch,
            change_summary=adjustment.change_summary,
            rationale_json=adjustment.rationale_json,
            status=adjustment.status,
            policy_version=adjustment.policy_version,
            automation_allowed=adjustment.automation_allowed,
            base_plan_version=adjustment.base_plan_version,
            expires_at=adjustment.expires_at,
            risk_level=adjustment.risk_level,
            requires_confirmation=adjustment.requires_confirmation,
        )
        record.plan_patch = {**(record.plan_patch or {}), "operations": adjustment.operations}
        self.session.add(record)
        self.session.flush()
        return adjustment.model_copy(update={"adjustment_id": record.id})
