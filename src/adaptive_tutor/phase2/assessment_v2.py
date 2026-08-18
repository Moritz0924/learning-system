"""Pure LangGraph orchestration for prepared Assessment V2 contexts.

This module intentionally has no repositories, SQLAlchemy imports, RAG client, or
session mutation. The application service prepares contexts and persists the
actions returned by this graph in short transactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.assessment.contracts import (
    AssessmentGenerationBundleV2,
    AssessmentGenerationContextV2,
    AssessmentGradeBundleV2,
    AssessmentGradingContextV2,
    MasteryEvidenceV2,
    MasteryUpdateV2,
    ObserverDecisionV2,
    ObserverSignalBundleV2,
    PlanProposalV2,
)


class PreparedAssessmentSubmissionV2(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=False)

    grading_context: AssessmentGradingContextV2
    previous_mastery: dict[str, dict]
    evidence: list[MasteryEvidenceV2] = Field(default_factory=list)
    observer_signals: ObserverSignalBundleV2


class AssessmentV2WorkflowAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str
    context_hash: str | None = None


class AssessmentV2WorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_bundle: AssessmentGenerationBundleV2 | None = None
    grade_bundle: AssessmentGradeBundleV2 | None = None
    mastery_updates: list[MasteryUpdateV2] = Field(default_factory=list)
    observer_decision: ObserverDecisionV2 | None = None
    plan_proposal: PlanProposalV2 | None = None
    actions: list[AssessmentV2WorkflowAction] = Field(default_factory=list)


@dataclass(frozen=True)
class AssessmentV2WorkflowPorts:
    generator: Callable[[AssessmentGenerationContextV2], AssessmentGenerationBundleV2]
    grader: Callable[[AssessmentGradingContextV2], AssessmentGradeBundleV2] | None = None
    mastery: Callable[[dict[str, dict], list[MasteryEvidenceV2]], list[MasteryUpdateV2]] | None = None
    observer: Callable[[ObserverSignalBundleV2], ObserverDecisionV2] | None = None
    plan_policy: Callable[[ObserverDecisionV2], PlanProposalV2] | None = None


class AssessmentV2WorkflowState(TypedDict, total=False):
    operation: str
    generation_context: AssessmentGenerationContextV2
    submission: PreparedAssessmentSubmissionV2
    generation_bundle: AssessmentGenerationBundleV2
    grade_bundle: AssessmentGradeBundleV2
    mastery_updates: list[MasteryUpdateV2]
    observer_decision: ObserverDecisionV2
    plan_proposal: PlanProposalV2
    actions: list[AssessmentV2WorkflowAction]


class Phase2AssessmentV2Workflow:
    """Run deterministic workflow transitions over detached, validated contexts."""

    def __init__(self, ports: AssessmentV2WorkflowPorts) -> None:
        self.ports = ports
        self.graph = self._build_graph()

    def run_generation(self, context: AssessmentGenerationContextV2) -> AssessmentV2WorkflowResult:
        output = self.graph.invoke(
            {
                "operation": "generate",
                "generation_context": context,
                "actions": [],
            }
        )
        return AssessmentV2WorkflowResult(
            generation_bundle=output.get("generation_bundle"),
            actions=output.get("actions", []),
        )

    def run_submission(self, prepared: PreparedAssessmentSubmissionV2) -> AssessmentV2WorkflowResult:
        if any(port is None for port in (self.ports.grader, self.ports.mastery, self.ports.observer, self.ports.plan_policy)):
            raise RuntimeError("Assessment V2 submission workflow requires grading, mastery, observer, and plan ports.")
        output = self.graph.invoke({"operation": "submit", "submission": prepared, "actions": []})
        return AssessmentV2WorkflowResult(
            grade_bundle=output.get("grade_bundle"),
            mastery_updates=output.get("mastery_updates", []),
            observer_decision=output.get("observer_decision"),
            plan_proposal=output.get("plan_proposal"),
            actions=output.get("actions", []),
        )

    def run_grading(self, context: AssessmentGradingContextV2) -> AssessmentV2WorkflowResult:
        if self.ports.grader is None:
            raise RuntimeError("Assessment V2 grading workflow requires a grading port.")
        output = self.graph.invoke({"operation": "grade_only", "submission": PreparedAssessmentSubmissionV2.model_construct(grading_context=context), "actions": []})
        return AssessmentV2WorkflowResult(
            grade_bundle=output.get("grade_bundle"),
            actions=output.get("actions", []),
        )

    def _build_graph(self):
        graph = StateGraph(AssessmentV2WorkflowState)
        graph.add_node("generate", self._generate)
        graph.add_node("grade", self._grade)
        graph.add_node("mastery", self._mastery)
        graph.add_node("observer", self._observer)
        graph.add_node("plan", self._plan)
        graph.set_conditional_entry_point(
            lambda state: "generate" if state["operation"] == "generate" else "grade",
            {"generate": "generate", "grade": "grade"},
        )
        graph.add_edge("generate", END)
        graph.add_conditional_edges(
            "grade",
            lambda state: "end" if state["operation"] == "grade_only" else "mastery",
            {"end": END, "mastery": "mastery"},
        )
        graph.add_edge("mastery", "observer")
        graph.add_edge("observer", "plan")
        graph.add_edge("plan", END)
        return graph.compile()

    def _generate(self, state: AssessmentV2WorkflowState) -> dict:
        context = state["generation_context"]
        return {
            "generation_bundle": self.ports.generator(context),
            "actions": [*state.get("actions", []), AssessmentV2WorkflowAction(action_type="assessment_generated", context_hash=context.context_hash)],
        }

    def _grade(self, state: AssessmentV2WorkflowState) -> dict:
        prepared = state["submission"]
        assert self.ports.grader is not None
        return {
            "grade_bundle": self.ports.grader(prepared.grading_context),
            "actions": [*state.get("actions", []), AssessmentV2WorkflowAction(action_type="assessment_graded", context_hash=prepared.grading_context.context_hash)],
        }

    def _mastery(self, state: AssessmentV2WorkflowState) -> dict:
        prepared = state["submission"]
        assert self.ports.mastery is not None
        return {
            "mastery_updates": self.ports.mastery(prepared.previous_mastery, prepared.evidence),
            "actions": [*state.get("actions", []), AssessmentV2WorkflowAction(action_type="mastery_calculated")],
        }

    def _observer(self, state: AssessmentV2WorkflowState) -> dict:
        prepared = state["submission"]
        assert self.ports.observer is not None
        return {
            "observer_decision": self.ports.observer(prepared.observer_signals),
            "actions": [*state.get("actions", []), AssessmentV2WorkflowAction(action_type="observer_decided")],
        }

    def _plan(self, state: AssessmentV2WorkflowState) -> dict:
        assert self.ports.plan_policy is not None
        decision = state["observer_decision"]
        return {
            "plan_proposal": self.ports.plan_policy(decision),
            "actions": [*state.get("actions", []), AssessmentV2WorkflowAction(action_type="plan_proposed")],
        }
