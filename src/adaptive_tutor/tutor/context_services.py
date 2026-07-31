"""Context loading, retrieval, and teacher services at the tutor boundary."""

from __future__ import annotations

from collections.abc import MutableMapping
import os
from uuid import uuid4

from .contracts import TutorRuntimeDependencies
from .history import conversation_context
from .identifiers import stable_request_hash
from .models import EvidenceState
from .state import LegacyTutorStateAdapter


class SessionContextService:
    def __init__(self, state_adapter: LegacyTutorStateAdapter | None = None):
        self.state_adapter = state_adapter or LegacyTutorStateAdapter()

    def load(self, state: MutableMapping[str, object], dependencies: TutorRuntimeDependencies) -> MutableMapping[str, object]:
        request = state["request"]
        prepared_context = state.get("prepared_context")
        snapshot = (
            prepared_context.state_snapshot
            if getattr(request, "trigger_type") == "chat" and prepared_context is not None
            else dependencies.state_repository.load_context(getattr(request, "user_id"), getattr(request, "goal_id"))
        )
        tutor_context = None
        if getattr(request, "trigger_type") == "chat":
            tutor_context = (
                prepared_context.tutor_context
                if prepared_context is not None
                else dependencies.tutor_context_factory(snapshot)
            )
        snapshot_dict = dict(snapshot)
        state.update(
            {
                "state_snapshot": snapshot_dict,
                "observer_signals": snapshot_dict.get("observer_signals", {}),
            }
        )
        if tutor_context is not None:
            state["tutor_context"] = tutor_context

        workflow_state = self.state_adapter.ingress(state)
        workflow_state = workflow_state.model_copy(
            update={
                "learning": workflow_state.learning.model_copy(
                    update={
                        "active_plan": snapshot_dict.get("active_plan", {}),
                        "current_task": snapshot_dict.get("current_task"),
                        "mastery_summary": snapshot_dict.get("mastery_summary", {}),
                        "recent_learning_events": snapshot_dict.get("recent_learning_events", []),
                    }
                )
            }
        )
        self.state_adapter.egress(state, workflow_state)
        _audit_log(state).append({"node": "load_context", "status": "ok"})
        return state


class RetrievalService:
    def __init__(self, state_adapter: LegacyTutorStateAdapter | None = None):
        self.state_adapter = state_adapter or LegacyTutorStateAdapter()

    def retrieve(
        self,
        state: MutableMapping[str, object],
        dependencies: TutorRuntimeDependencies,
        *,
        citation_factory: object,
        action_factory: object,
    ) -> MutableMapping[str, object]:
        request = state["request"]
        prepared_context = state.get("prepared_context")
        if prepared_context is not None:
            chunks = list(prepared_context.retrieved_context)
            retrieval_status = prepared_context.retrieval_status
            degraded_reason = prepared_context.degraded_reason
        else:
            chunks = list(
                dependencies.rag_repository.retrieve(
                    getattr(request, "user_message"), top_k=5, user_id=getattr(request, "user_id")
                )
            )
            retrieval_status = getattr(
                dependencies.rag_repository, "last_retrieval_status", "grounded" if chunks else "no_context"
            )
            degraded_reason = getattr(dependencies.rag_repository, "degraded_reason", None)
        state["retrieved_context"] = chunks
        state["citations"] = chunks
        state["retrieval_run_id"] = (
            prepared_context.retrieval_run_id
            if prepared_context is not None and prepared_context.retrieval_run_id
            else str(uuid4())
        )
        state["tutor_context"] = state["tutor_context"].model_copy(
            update={
                "rag_citations": [
                    citation_factory(
                        chunk_id=chunk.chunk_id,
                        document_id=chunk.document_id,
                        citation_label=chunk.citation_label,
                        source_title=chunk.source_title,
                        source_url=chunk.source_url,
                        trusted_level=chunk.trusted_level,
                    )
                    for chunk in chunks
                ]
            }
        )
        workflow_state = self.state_adapter.ingress(state)
        workflow_state = workflow_state.model_copy(
            update={"evidence": EvidenceState(retrieved_chunk_ids=[chunk.chunk_id for chunk in chunks])}
        )
        self.state_adapter.egress(state, workflow_state)
        state.setdefault("workflow_actions", []).append(
            action_factory(
                action_type="record_tool_call",
                audit_payload={
                    "tool_name": "rag.retrieve",
                    "request_hash": stable_request_hash({"user_message": getattr(request, "user_message")}),
                    "response_summary": {
                        "chunk_count": len(chunks),
                        "retrieval_status": retrieval_status,
                        "degraded_reason": degraded_reason,
                    },
                    "status": "failed" if retrieval_status == "failed" else "success",
                },
            )
        )
        _audit_log(state).append(
            {"node": "retrieve_context", "chunk_count": len(chunks), "retrieval_status": retrieval_status, "degraded_reason": degraded_reason}
        )
        return state


class TeacherService:
    def teach(self, state: MutableMapping[str, object], dependencies: TutorRuntimeDependencies) -> MutableMapping[str, object]:
        request = state["request"]
        state["route"] = "teaching"
        prompt = getattr(request, "user_message") or "Explain the current task."
        state["teacher_prompt"] = prompt
        kwargs = {
            "role": "teacher",
            "prompt": prompt,
            "tutor_context": state["tutor_context"],
            "conversation_context": conversation_context(state["workflow_state"].conversation),
            "context": [*state.get("retrieved_context", []), *state.get("tool_results", [])],
        }
        if _structured_answer_enabled():
            kwargs["response_envelope"] = (
                "Return only a JSON object with answer, claims, citations, "
                "insufficient_evidence, and missing_information. "
                "Every claim citation must refer to the supplied evidence."
            )
        try:
            state["final_answer"] = dependencies.llm_client.complete(**kwargs)
        except TypeError:
            kwargs.pop("response_envelope", None)
            state["final_answer"] = dependencies.llm_client.complete(**kwargs)
        _audit_log(state).append({"node": "teacher", "status": "ok"})
        return state


def _audit_log(state: MutableMapping[str, object]) -> list[dict[str, object]]:
    audit_log = state.setdefault("audit_log", [])
    if not isinstance(audit_log, list):
        raise TypeError("legacy tutor state audit_log must be a list")
    return audit_log


def _structured_answer_enabled() -> bool:
    return os.getenv("FEATURE_STRUCTURED_ANSWER_V2", "false").strip().lower() in {"1", "true", "yes", "on"}
