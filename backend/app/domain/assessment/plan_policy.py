from __future__ import annotations

from .contracts import ObserverDecisionV2, PlanProposalV2


def build_plan_proposal(decision: ObserverDecisionV2) -> PlanProposalV2:
    patches = {
        "keep": {"no_change": True},
        "manual_review": {"no_change": True},
        "reduce": {"load_multiplier": 0.8, "defer_nonessential": True},
        "remediate": {"insert_review": True, "review_task_count": 2},
        "advance": {"unlock_next_nodes": True, "increase_difficulty": 1},
    }
    summaries = {
        "keep": {"decision": "keep", "changes": [], "summary": "Keep the current plan."},
        "manual_review": {"decision": "manual_review", "changes": [], "summary": "Review the assessment evidence before changing the plan."},
        "reduce": {"decision": "reduce", "changes": [{"type": "load", "label": "Reduce future workload"}]},
        "remediate": {"decision": "remediate", "changes": [{"type": "review", "label": "Add targeted review work"}]},
        "advance": {"decision": "advance", "changes": [{"type": "progression", "label": "Unlock the next learning node"}]},
    }
    return PlanProposalV2(
        decision=decision.decision,
        automation_allowed=decision.automation_allowed,
        plan_patch=patches[decision.decision],
        change_summary=summaries[decision.decision],
        rationale_json={
            "decision": decision.decision,
            "rationale": decision.user_facing_rationale,
            "reason_codes": decision.reason_codes,
        },
    )
