from __future__ import annotations

import os
from time import perf_counter
from uuid import uuid4
from collections.abc import Mapping
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from .ports import Phase2Dependencies
from .schemas import (
    PreparedTutorContext,
    TutorRagCitation,
    TutorRunRequest,
    TutorRunResult,
    TutorState,
    WorkflowAction,
)
from adaptive_tutor.tutor.agent_contracts import AgentDecision, AgentLoopState, AgentToolObservation
from adaptive_tutor.tutor.agent_controller import (
    AgentControllerService,
    agent_loop_policy_from_env,
    load_agent_loop_state,
    save_agent_loop_state,
)
from adaptive_tutor.tutor.memory import MEMORY_GATE_POLICY_VERSION
from adaptive_tutor.tutor.history import (
    HistoryPolicy,
    conversation_context,
    record_completed_turn,
    restore_safe_conversation,
)
from adaptive_tutor.tutor.identifiers import stable_request_hash
from adaptive_tutor.tutor.models import TutorWorkflowState
from adaptive_tutor.tutor.services import (
    AssessmentService,
    GroundingService,
    IntentRouter,
    MemoryService,
    ObserverService,
    PlanningService,
    RetrievalService,
    SessionContextService,
    TeacherService,
    WorkflowPersistenceService,
)
from adaptive_tutor.tutor.state import LegacyTutorStateAdapter
from .assessment import build_assessment_draft, grade_assessment_attempt, mastery_updates_from_attempt
from .assessment_intelligence import build_intelligent_assessment_draft, mastery_updates_from_attempt_v2
from .replanning import build_observer_signals, decide_observer_action_from_signals, generate_plan_adjustment
from adaptive_tutor.tutor.grounding import GroundingPipeline, build_retrieval_snapshot
from adaptive_tutor.tutor.t3_contracts import (
    GroundingStatus,
    Thread3ErrorCode,
    canonical_json_hash,
    feature_flags_from_env,
)
from adaptive_tutor.tutor.tool_router import ToolRouterError


class Phase2TutorEngine:
    def __init__(
        self,
        dependencies: Phase2Dependencies,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        history_policy: HistoryPolicy | None = None,
    ):
        self.dependencies = dependencies
        self.checkpointer = checkpointer or InMemorySaver()
        self.history_policy = history_policy or HistoryPolicy()
        self._pending_chat_history: dict[str, TutorWorkflowState] = {}
        self.session_context_service = SessionContextService()
        self.intent_router = IntentRouter()
        self.retrieval_service = RetrievalService()
        self.teacher_service = TeacherService()
        self.grounding_service = GroundingService()
        self.assessment_service = AssessmentService()
        self.observer_service = ObserverService()
        self.planning_service = PlanningService()
        self.memory_service = MemoryService()
        self.workflow_persistence_service = WorkflowPersistenceService()
        self.state_adapter = LegacyTutorStateAdapter()
        self.agent_controller = AgentControllerService()
        self.graph = self._build_graph()

    def run(
        self,
        request: TutorRunRequest,
        *,
        prepared_context: PreparedTutorContext | None = None,
        defer_history_checkpoint: bool = False,
    ) -> TutorRunResult:
        started = perf_counter()
        state: dict[str, Any] = {
            "request": request,
            "thread_id": request.thread_id,
            "user_id": request.user_id,
            "goal_id": request.goal_id,
            "trigger_type": request.trigger_type,
            "user_message": request.user_message,
            "audit_log": [],
            "citations": [],
            "mastery_updates": [],
            "workflow_actions": [],
            "retrieval_run_id": "",
            "grounding_status": None,
            "insufficient_evidence": False,
            "missing_information": [],
            "public_citations": [],
            "tool_results": [],
            "request_hash": stable_request_hash(request),
        }
        if prepared_context is not None:
            state["prepared_context"] = prepared_context
        workflow_state = self.state_adapter.ingress(
            state,
            graph_version="phase2-v1",
        )
        workflow_state = restore_safe_conversation(
            workflow_state,
            self._saved_workflow_state(request.thread_id),
            policy=self.history_policy,
        )
        self.state_adapter.egress(state, workflow_state)
        config = {"configurable": {"thread_id": request.thread_id}}
        output = self.graph.invoke(state, config=config)
        self.state_adapter.egress(output, self.state_adapter.ingress(output, graph_version="phase2-v1"))
        if request.trigger_type == "chat":
            if defer_history_checkpoint:
                self._pending_chat_history[request.thread_id] = output[
                    "workflow_state"
                ]
            else:
                output["workflow_state"] = record_completed_turn(
                    output["workflow_state"],
                    user_message=request.user_message,
                    assistant_message=output.get("final_answer", ""),
                    policy=self.history_policy,
                )
                self.state_adapter.egress(output, output["workflow_state"])
                self._update_workflow_checkpoint(
                    request.thread_id,
                    output["workflow_state"],
                )
        latency_ms = int((perf_counter() - started) * 1000)
        audit_payload: dict[str, Any] = {
            "thread_id": request.thread_id,
            "user_id": request.user_id,
            "goal_id": request.goal_id,
            "graph_name": "phase2_tutor_graph",
            "graph_version": "phase2-v1",
            "trigger_type": request.trigger_type,
            "status": "success",
            "latency_ms": latency_ms,
            "error_message": None,
            "run_id": output["workflow_state"].execution.run_id,
            "request_hash": state["request_hash"],
            "node_trace": list(output.get("audit_log", [])),
        }
        if request.trigger_type == "chat":
            memory_selection = prepared_context.memory_selection if prepared_context is not None else None
            audit_payload["memory_context"] = {
                "selected_memory_ids": [] if memory_selection is None else memory_selection.selected_memory_ids,
                "policy_version": "memory-context-v1" if memory_selection is None else memory_selection.policy_version,
            }
        memory_decisions = output.get("memory_decisions", [])
        if memory_decisions:
            audit_payload["memory_gate"] = {
                "policy_version": MEMORY_GATE_POLICY_VERSION,
                "items": [
                    {
                        "candidate_id": decision.candidate.candidate_id,
                        "origin": decision.candidate.origin,
                        "decision": decision.decision,
                        "reason_code": decision.reason_code,
                    }
                    for decision in memory_decisions
                ],
            }
        agent_loop = load_agent_loop_state(output["workflow_state"])
        agent_flags = feature_flags_from_env(os.environ)
        output.setdefault("workflow_actions", []).append(
            WorkflowAction(
                action_type="record_agent_run",
                audit_payload=audit_payload,
            )
        )
        return TutorRunResult(
            route=output.get("route", "teaching"),
            final_answer=output.get("final_answer", ""),
            citations=output.get("citations", []),
            assessment_draft=output.get("assessment_draft"),
            assessment_result=output.get("assessment_result"),
            mastery_updates=output.get("mastery_updates", []),
            observer_decision=output.get("observer_decision"),
            plan_adjustment=output.get("plan_adjustment"),
            audit_log=output.get("audit_log", []),
            workflow_actions=output.get("workflow_actions", []),
            memory_decisions=memory_decisions,
            grounding_status=output.get("grounding_status"),
            insufficient_evidence=output.get("insufficient_evidence", False),
            missing_information=output.get("missing_information", []),
            public_citations=output.get("public_citations", []),
            runtime_metadata={
                "agent": {
                    "enabled": any(item.get("node") == "agent_decide" for item in output.get("audit_log", [])),
                    "feature_flag": agent_flags["FEATURE_AGENT_TOOL_LOOP_V1"],
                    "decision_count": agent_loop.decision_count,
                    "tool_call_count": len(output["workflow_state"].execution.tool_calls),
                    "stop_reason": agent_loop.stop_reason,
                }
            },
        )

    def finalize_chat_history(
        self,
        request: TutorRunRequest,
        *,
        assistant_message: str,
    ) -> None:
        pending = self._pending_chat_history.pop(request.thread_id, None)
        if pending is None:
            raise RuntimeError("chat history is not pending finalization")
        completed = record_completed_turn(
            pending,
            user_message=request.user_message,
            assistant_message=assistant_message,
            policy=self.history_policy,
        )
        self._update_workflow_checkpoint(request.thread_id, completed)

    def _update_workflow_checkpoint(
        self,
        thread_id: str,
        workflow_state: TutorWorkflowState,
    ) -> None:
        self.graph.update_state(
            {"configurable": {"thread_id": thread_id}},
            {"workflow_state": workflow_state},
        )

    def _build_graph(self):
        graph = StateGraph(TutorState)
        graph.add_node("load_context", self._load_context)
        graph.add_node("diagnosis", self._diagnosis)
        graph.add_node("retrieve_context", self._retrieve_context)
        graph.add_node("agent_decide", self._agent_decide)
        graph.add_node("tool_router", self._tool_router)
        graph.add_node("teacher", self._teacher)
        graph.add_node("build_assessment", self._build_assessment)
        graph.add_node("grade_assessment", self._grade_assessment)
        graph.add_node("observer", self._observer)
        graph.add_node("planner", self._planner)
        graph.add_node("memory_gate", self._memory_gate)
        graph.add_node("persist", self._persist)

        graph.set_entry_point("load_context")
        graph.add_conditional_edges(
            "load_context",
            self._route_after_load,
            {
                "diagnosis": "diagnosis",
                "retrieve_context": "retrieve_context",
                "build_assessment": "build_assessment",
                "grade_assessment": "grade_assessment",
                "observer": "observer",
                "planner": "planner",
            },
        )
        graph.add_edge("diagnosis", "planner")
        graph.add_conditional_edges(
            "retrieve_context",
            self._route_after_retrieval,
            {
                "agent_decide": "agent_decide",
                "tool_router": "tool_router",
                "teacher": "teacher",
            },
        )
        graph.add_conditional_edges(
            "agent_decide",
            self._route_after_agent_decision,
            {"tool_router": "tool_router", "teacher": "teacher"},
        )
        graph.add_conditional_edges(
            "tool_router",
            self._route_after_tool,
            {"agent_decide": "agent_decide", "teacher": "teacher"},
        )
        graph.add_edge("teacher", "observer")
        graph.add_edge("build_assessment", "persist")
        graph.add_edge("grade_assessment", "observer")
        graph.add_conditional_edges(
            "observer",
            self._route_after_observer,
            {"planner": "planner", "memory_gate": "memory_gate"},
        )
        graph.add_edge("planner", "memory_gate")
        graph.add_edge("memory_gate", "persist")
        graph.add_edge("persist", END)
        return graph.compile(checkpointer=self.checkpointer)

    def _saved_workflow_state(self, thread_id: str) -> TutorWorkflowState | None:
        saved = self.checkpointer.get_tuple(
            {"configurable": {"thread_id": thread_id}}
        )
        if saved is None:
            return None
        value = saved.checkpoint.get("channel_values", {}).get("workflow_state")
        if isinstance(value, TutorWorkflowState):
            return value
        if isinstance(value, Mapping):
            try:
                return TutorWorkflowState.model_validate(value)
            except ValueError:
                return None
        return None

    def _load_context(self, state: dict) -> dict:
        return self.session_context_service.load(state, self.dependencies)

    def _diagnosis(self, state: dict) -> dict:
        state["route"] = "diagnostic"
        state["final_answer"] = "Baseline diagnosis received; preparing an initial plan patch."
        state["audit_log"].append({"node": "diagnosis", "status": "ok"})
        return state

    def _retrieve_context(self, state: dict) -> dict:
        return self.retrieval_service.retrieve(
            state, self.dependencies, citation_factory=TutorRagCitation, action_factory=WorkflowAction
        )

    def _agent_decide(self, state: dict) -> dict:
        policy = agent_loop_policy_from_env(os.environ)
        workflow_state = state["workflow_state"]
        loop_state = load_agent_loop_state(workflow_state)
        if loop_state.decision_count >= policy.max_decisions:
            loop_state = loop_state.model_copy(
                update={
                    "active": False,
                    "max_decisions": policy.max_decisions,
                    "pending_tool_call": None,
                    "last_reason_code": "budget_exhausted",
                    "stop_reason": "budget_exhausted",
                }
            )
            decision = AgentDecision(action="answer", reason_code="budget_exhausted")
        else:
            loop_state = loop_state.model_copy(
                update={
                    "active": True,
                    "max_decisions": policy.max_decisions,
                    "pending_tool_call": None,
                    "stop_reason": None,
                }
            )
            router = getattr(self.dependencies, "tool_router", None)
            tools = list(router.list_agent_tools()) if router is not None else []
            decision = self.agent_controller.decide(
                state,
                self.dependencies,
                tools=tools,
                policy=policy,
            )
            loop_state = loop_state.model_copy(
                update={
                    "active": decision.action == "call_tool",
                    "decision_count": loop_state.decision_count + 1,
                    "pending_tool_call": decision.tool_call,
                    "last_reason_code": decision.reason_code,
                    "stop_reason": None if decision.action == "call_tool" else decision.reason_code,
                }
            )
        state["workflow_state"] = save_agent_loop_state(workflow_state, loop_state)
        state["agent_decision"] = decision
        state.setdefault("audit_log", []).append(
            {
                "node": "agent_decide",
                "action": decision.action,
                "reason_code": decision.reason_code,
                "decision_count": loop_state.decision_count,
                "stop_reason": loop_state.stop_reason,
            }
        )
        return state

    def _teacher(self, state: dict) -> dict:
        state = self.teacher_service.teach(state, self.dependencies)
        flags = feature_flags_from_env(os.environ)
        if flags["FEATURE_STRUCTURED_ANSWER_V2"] and flags["FEATURE_GROUNDING_V2"]:
            chunks = list(state.get("retrieved_context", []))
            snapshot = build_retrieval_snapshot(
                run_id=state["workflow_state"].execution.run_id,
                retrieval_run_id=state.get("retrieval_run_id") or str(uuid4()),
                chunks=chunks,
            )

            def repair(prompt: str) -> str:
                kwargs = {
                    "role": "teacher_repair",
                    "prompt": prompt,
                    "tutor_context": state["tutor_context"],
                    "conversation_context": conversation_context(state["workflow_state"].conversation),
                    "context": chunks,
                    "model_tier": "pro",
                }
                try:
                    return self.dependencies.llm_client.complete(**kwargs)
                except TypeError:
                    return self.dependencies.llm_client.complete(
                        role=kwargs["role"],
                        prompt=kwargs["prompt"],
                        tutor_context=kwargs["tutor_context"],
                        conversation_context=kwargs["conversation_context"],
                        context=kwargs["context"],
                    )

            evaluation = GroundingPipeline().evaluate(
                raw=state["final_answer"],
                question=getattr(state["request"], "user_message", ""),
                chunks=chunks,
                snapshot=snapshot,
                repair=repair,
            )
            state["retrieval_snapshot"] = snapshot
            state["grounding_status"] = evaluation.status.value
            state["public_citations"] = evaluation.public_citations
            state["citations"] = evaluation.referenced_chunks
            state["insufficient_evidence"] = evaluation.status is GroundingStatus.INSUFFICIENT_EVIDENCE
            state["missing_information"] = list(
                evaluation.draft.missing_information if evaluation.draft is not None else ()
            )
            if evaluation.status in {GroundingStatus.SAFE_REFUSAL, GroundingStatus.VALIDATION_ERROR}:
                state["final_answer"] = (
                    "当前资料无法可靠支持该回答。"
                    if evaluation.status is GroundingStatus.SAFE_REFUSAL
                    else "暂时无法生成可验证的结构化回答。"
                )
            elif evaluation.draft is not None:
                state["final_answer"] = evaluation.draft.answer
            state["audit_log"].append(
                {
                    "node": "grounding",
                    "status": evaluation.status.value,
                    "repair_count": evaluation.repair_count,
                    "snapshot_id": snapshot.snapshot_id,
                }
            )
        else:
            grounding = self.grounding_service.validate(
                answer=state["final_answer"],
                retrieved_chunk_ids=[chunk.chunk_id for chunk in state.get("retrieved_context", [])],
            )
            workflow_state = state["workflow_state"]
            state["workflow_state"] = workflow_state.model_copy(
                update={"evidence": workflow_state.evidence.model_copy(update={"grounding_result": grounding.model_dump()})}
            )
            return state
        workflow_state = state["workflow_state"]
        state["workflow_state"] = workflow_state.model_copy(
            update={
                "evidence": workflow_state.evidence.model_copy(
                    update={
                        "grounding_result": {
                            "status": state["grounding_status"],
                            "snapshot_id": state["retrieval_snapshot"].snapshot_id,
                        }
                    }
                )
            }
        )
        return state

    def _tool_router(self, state: dict) -> dict:
        request = state["request"]
        router = getattr(self.dependencies, "tool_router", None)
        if router is None:
            state["audit_log"].append({"node": "tool_router", "status": "not_configured"})
            return state
        loop_state = load_agent_loop_state(state["workflow_state"])
        autonomous = loop_state.active and loop_state.pending_tool_call is not None
        if autonomous:
            tool_request = loop_state.pending_tool_call.model_dump(mode="json")
            tool_name = tool_request["tool_name"]
            arguments = tool_request["arguments"]
            fingerprint = canonical_json_hash(tool_request)
            try:
                result = router.execute_agent(
                    run_id=state["workflow_state"].execution.run_id,
                    user_id=getattr(request, "user_id"),
                    tool_name=tool_name,
                    arguments=arguments,
                )
            except ToolRouterError as exc:
                observation = AgentToolObservation(
                    tool_name=tool_name,
                    arguments=arguments,
                    fingerprint=fingerprint,
                    status="failed",
                    error_code=exc.code.value,
                )
                stop_reason = None if exc.code in {
                    Thread3ErrorCode.TOOL_TIMEOUT,
                    Thread3ErrorCode.TOOL_EXECUTION_FAILED,
                } else exc.code.value
                self._record_agent_tool_result(
                    state,
                    loop_state,
                    observation,
                    stop_reason=stop_reason,
                )
                self._record_tool_action(
                    state,
                    tool_name=tool_name,
                    fingerprint=fingerprint,
                    status="failed",
                    error_code=exc.code.value,
                )
                state["audit_log"].append(
                    {
                        "node": "tool_router",
                        "status": "failed",
                        "error_code": exc.code.value,
                        "stop_reason": stop_reason,
                    }
                )
                return state

            duplicate = result.cache_hit and any(
                item.fingerprint == result.fingerprint for item in loop_state.observations
            )
            observation = AgentToolObservation(
                tool_name=tool_name,
                arguments=arguments,
                fingerprint=result.fingerprint,
                status="success",
                value=result.value,
                cache_hit=result.cache_hit,
                truncated=result.truncated,
            )
            self._record_agent_tool_result(
                state,
                loop_state,
                observation,
                stop_reason="duplicate_tool_call" if duplicate else None,
            )
            if not duplicate:
                state.setdefault("tool_results", []).append(result.value)
            self._record_tool_action(
                state,
                tool_name=tool_name,
                fingerprint=result.fingerprint,
                status="success",
                cache_hit=result.cache_hit,
                truncated=result.truncated,
            )
            state["audit_log"].append(
                {
                    "node": "tool_router",
                    "status": "success",
                    "cache_hit": result.cache_hit,
                    "truncated": result.truncated,
                    "stop_reason": "duplicate_tool_call" if duplicate else None,
                }
            )
            return state

        tool_request = getattr(request, "metadata", {}).get("tool_request", {})
        try:
            result = router.execute(
                run_id=state["workflow_state"].execution.run_id,
                user_id=getattr(request, "user_id"),
                tool_name=tool_request.get("tool_name", ""),
                arguments=tool_request.get("arguments", {}),
            )
            state.setdefault("tool_results", []).append(result.value)
            state["audit_log"].append(
                {
                    "node": "tool_router",
                    "status": "success",
                    "cache_hit": result.cache_hit,
                    "truncated": result.truncated,
                }
            )
        except ToolRouterError as exc:
            state["audit_log"].append({"node": "tool_router", "status": "failed", "error_code": exc.code.value})
        return state

    def _record_agent_tool_result(
        self,
        state: dict,
        loop_state: AgentLoopState,
        observation: AgentToolObservation,
        *,
        stop_reason: str | None,
    ) -> None:
        active = stop_reason is None
        updated_loop = loop_state.model_copy(
            update={
                "active": active,
                "pending_tool_call": None,
                "observations": [*loop_state.observations, observation],
                "stop_reason": stop_reason,
            }
        )
        workflow_state = state["workflow_state"]
        execution = workflow_state.execution.model_copy(
            update={
                "agent_state": updated_loop.model_dump(mode="json"),
                "tool_calls": [
                    *workflow_state.execution.tool_calls,
                    {
                        "tool_name": observation.tool_name,
                        "fingerprint": observation.fingerprint,
                        "status": observation.status,
                        "cache_hit": observation.cache_hit,
                        "truncated": observation.truncated,
                        "error_code": observation.error_code,
                    },
                ],
            }
        )
        state["workflow_state"] = workflow_state.model_copy(update={"execution": execution})

    def _record_tool_action(
        self,
        state: dict,
        *,
        tool_name: str,
        fingerprint: str,
        status: str,
        cache_hit: bool = False,
        truncated: bool = False,
        error_code: str | None = None,
    ) -> None:
        request = state["request"]
        state.setdefault("workflow_actions", []).append(
            WorkflowAction(
                action_type="record_tool_call",
                user_id=getattr(request, "user_id", None),
                goal_id=getattr(request, "goal_id", None),
                audit_payload={
                    "tool_name": tool_name,
                    "request_hash": fingerprint,
                    "status": status,
                    "cache_hit": cache_hit,
                    "truncated": truncated,
                    "error_code": error_code,
                },
            )
        )

    def _build_assessment(self, state: dict) -> dict:
        flags = feature_flags_from_env(os.environ)
        builder = build_intelligent_assessment_draft if flags["FEATURE_ASSESSMENT_INTELLIGENCE_V2"] else build_assessment_draft
        return self.assessment_service.build_draft(state, build_assessment=builder)

    def _grade_assessment(self, state: dict) -> dict:
        flags = feature_flags_from_env(os.environ)
        updater = mastery_updates_from_attempt_v2 if flags["FEATURE_ASSESSMENT_INTELLIGENCE_V2"] else mastery_updates_from_attempt
        return self.assessment_service.grade_attempt(
            state,
            self.dependencies,
            grade_assessment=grade_assessment_attempt,
            mastery_updates=updater,
        )

    def _observer(self, state: dict) -> dict:
        return self.observer_service.observe(
            state,
            build_signals=build_observer_signals,
            decide_action=decide_observer_action_from_signals,
        )

    def _planner(self, state: dict) -> dict:
        return self.planning_service.plan(
            state,
            decide_action=decide_observer_action_from_signals,
            generate_adjustment=generate_plan_adjustment,
        )

    def _memory_gate(self, state: dict) -> dict:
        return self.memory_service.decide(state, self.dependencies)

    def _persist(self, state: dict) -> dict:
        state.setdefault("workflow_actions", []).extend(
            self.workflow_persistence_service.build_actions(state=state, action_factory=WorkflowAction)
        )
        state["audit_log"].append({"node": "persist", "status": "ok"})
        return state

    def _route_after_load(self, state: dict) -> str:
        return self.intent_router.route_after_load(state["trigger_type"])

    def _route_after_retrieval(self, state: dict) -> str:
        flags = feature_flags_from_env(os.environ)
        metadata = getattr(state["request"], "metadata", {}) or {}
        if flags["FEATURE_MCP_TOOL_ROUTER_V2"] and metadata.get("tool_request"):
            return "tool_router"
        if (
            flags["FEATURE_AGENT_TOOL_LOOP_V1"]
            and state["trigger_type"] == "chat"
            and self._has_agent_tools()
        ):
            return "agent_decide"
        return "teacher"

    def _route_after_agent_decision(self, state: dict) -> str:
        decision = state.get("agent_decision")
        return "tool_router" if isinstance(decision, AgentDecision) and decision.action == "call_tool" else "teacher"

    def _route_after_tool(self, state: dict) -> str:
        loop_state = load_agent_loop_state(state["workflow_state"])
        return "agent_decide" if loop_state.active and not loop_state.stop_reason else "teacher"

    def _has_agent_tools(self) -> bool:
        router = getattr(self.dependencies, "tool_router", None)
        return bool(router is not None and hasattr(router, "list_agent_tools") and router.list_agent_tools())

    def _route_after_observer(self, state: dict) -> str:
        return self.intent_router.route_after_observer(
            trigger_type=state["trigger_type"], observer_decision=state.get("observer_decision")
        )
