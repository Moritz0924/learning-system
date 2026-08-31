"""Context loading, retrieval, and teacher services at the tutor boundary."""

from __future__ import annotations

from collections.abc import MutableMapping
import os
from uuid import uuid4

from .contracts import TutorRuntimeDependencies
from .evidence import evidence_from_retrieved_chunk, evidence_to_llm_context
from .history import conversation_context
from .identifiers import stable_request_hash
from .models import EvidenceState
from .state import LegacyTutorStateAdapter
from .t3_contracts import feature_flags_from_env


class TutorLocaleMismatchError(RuntimeError):
    """The model could not produce learner-visible text in the requested UI language."""


class SessionContextService:
    def __init__(self, state_adapter: LegacyTutorStateAdapter | None = None):
        self.state_adapter = state_adapter or LegacyTutorStateAdapter()

    def load(self, state: MutableMapping[str, object], dependencies: TutorRuntimeDependencies) -> MutableMapping[str, object]:
        request = state["request"]
        prepared_context = state.get("prepared_context")
        snapshot = (
            prepared_context.state_snapshot
            if getattr(request, "trigger_type") == "chat" and prepared_context is not None
            else dependencies.state_repository.load_context(
                getattr(request, "user_id"),
                getattr(request, "goal_id"),
                task_id=getattr(request, "task_id", None),
            )
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
        state["evidence_items"] = [evidence_from_retrieved_chunk(chunk) for chunk in chunks]
        state["selected_evidence_items"] = []
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
        flags = feature_flags_from_env(os.environ)
        context = (
            evidence_to_llm_context(state.get("selected_evidence_items", []))
            if flags["FEATURE_EVIDENCE_PIPELINE_V2"]
            else [*state.get("retrieved_context", []), *state.get("tool_results", [])]
        )
        kwargs = {
            "role": "teacher",
            "prompt": prompt,
            "tutor_context": state["tutor_context"],
            "conversation_context": conversation_context(state["workflow_state"].conversation),
            "context": context,
        }
        model_tier = (getattr(request, "metadata", {}) or {}).get("model_tier")
        if model_tier in {"flash", "pro"}:
            kwargs["model_tier"] = model_tier
        locale = (getattr(request, "metadata", {}) or {}).get("locale")
        language_instruction = _language_instruction(locale)
        if _structured_answer_enabled():
            kwargs["response_envelope"] = (
                "Return only a JSON object with answer, claims, citations, "
                "insufficient_evidence, and missing_information. "
                + (
                    "Use only evidence_id values present in the supplied EVIDENCE context. "
                    "Every factual claim must reference one or more supplied evidence_id values. "
                    "Never invent evidence_id."
                    if flags["FEATURE_EVIDENCE_PIPELINE_V2"]
                    else "Every claim citation must refer to the supplied evidence."
                )
            )
        if language_instruction:
            kwargs["response_envelope"] = " ".join(
                part for part in (language_instruction, kwargs.get("response_envelope")) if part
            )
        try:
            stream = getattr(dependencies.llm_client, "stream", None)
            on_delta = getattr(dependencies, "teacher_delta_callback", None)
            if (
                not language_instruction
                and callable(stream)
                and callable(on_delta)
                and getattr(dependencies.llm_client, "base_url", None)
            ):
                fragments: list[str] = []
                for fragment in stream(**kwargs):
                    fragments.append(fragment)
                    if "response_envelope" not in kwargs:
                        on_delta(fragment)
                state["final_answer"] = "".join(fragments)
            else:
                state["final_answer"] = dependencies.llm_client.complete(**kwargs)
        except TypeError:
            kwargs.pop("response_envelope", None)
            kwargs.pop("model_tier", None)
            state["final_answer"] = dependencies.llm_client.complete(**kwargs)
        if language_instruction:
            state["final_answer"] = _repair_locale_once(
                dependencies.llm_client,
                kwargs,
                str(state["final_answer"]),
                locale,
            )
        _audit_log(state).append({"node": "teacher", "status": "ok"})
        return state


def _audit_log(state: MutableMapping[str, object]) -> list[dict[str, object]]:
    audit_log = state.setdefault("audit_log", [])
    if not isinstance(audit_log, list):
        raise TypeError("legacy tutor state audit_log must be a list")
    return audit_log


def _structured_answer_enabled() -> bool:
    return os.getenv("FEATURE_STRUCTURED_ANSWER_V2", "false").strip().lower() in {"1", "true", "yes", "on"}


def _language_instruction(locale: object) -> str | None:
    if locale == "zh-CN":
        return "Respond only in Simplified Chinese for all learner-visible prose."
    if locale == "en-US":
        return "Respond only in English for all learner-visible prose."
    return None


def _repair_locale_once(llm_client: object, kwargs: dict, answer: str, locale: object) -> str:
    if _matches_locale(answer, locale):
        return answer
    repaired = llm_client.complete(
        **{
            **kwargs,
            "prompt": (
                "Rewrite the following assistant answer in the required output language. "
                "Return only the rewritten learner-visible answer, without commentary about the rewrite.\n\n"
                f"{answer}"
            ),
        }
    )
    if _matches_locale(repaired, locale):
        return repaired
    raise TutorLocaleMismatchError("tutor.locale_mismatch")


def _matches_locale(text: str, locale: object) -> bool:
    has_cjk = any("\u4e00" <= character <= "\u9fff" for character in text)
    if locale == "zh-CN":
        return has_cjk
    if locale == "en-US":
        return not has_cjk
    return True
