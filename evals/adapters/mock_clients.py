"""Explicit deterministic LLM clients for infrastructure-only smoke tests."""
from __future__ import annotations

import json
from time import perf_counter_ns
from typing import Any, Literal

from adaptive_tutor.phase2.telemetry import TimedLlmResult


MockScenario = Literal[
    "valid",
    "invalid_json",
    "invalid_citation",
    "missing_citations",
    "abstain",
    "wrong_answer",
]


class MockJsonLlmClient:
    def __init__(self, *, scenario: MockScenario = "valid") -> None:
        self.scenario = scenario
        self.call_count = 0
        self.last_kwargs: dict[str, Any] | None = None
        self.last_trace: TimedLlmResult | None = None

    def complete(self, **kwargs: Any) -> str:
        started = perf_counter_ns()
        self.call_count += 1
        self.last_kwargs = kwargs
        raw = self._render(context=kwargs.get("context") or [])
        elapsed = (perf_counter_ns() - started) / 1_000_000.0
        self.last_trace = TimedLlmResult(
            text=raw,
            model="evaluation-mock-json-v1",
            mode="mock",
            request_latency_ms=elapsed,
            parse_latency_ms=0.0,
            total_latency_ms=elapsed,
            retry_count=0,
        )
        return raw

    def _render(self, *, context: list[Any]) -> str:
        if self.scenario == "invalid_json":
            return "not-json"
        if self.scenario == "missing_citations":
            return json.dumps({"answer": "Mock answer"}, ensure_ascii=False)
        if self.scenario == "abstain":
            return json.dumps(
                {"answer": "当前检索资料不足，无法根据现有证据回答。", "citations": []},
                ensure_ascii=False,
            )
        if self.scenario == "wrong_answer":
            return json.dumps(
                {"answer": "文档明确给出了一个并不存在的答案。", "citations": []},
                ensure_ascii=False,
            )
        if self.scenario == "invalid_citation":
            citations = [{"chunk_id": "fabricated-chunk", "document_id": "fabricated-document"}]
        elif context:
            first = context[0]
            citations = [{
                "chunk_id": getattr(first, "chunk_id"),
                "document_id": getattr(first, "document_id"),
            }]
        else:
            citations = []
        return json.dumps(
            {"answer": "Mock grounded answer based on retrieved evidence.", "citations": citations},
            ensure_ascii=False,
        )


class HistoryAwareMockLlmClient(MockJsonLlmClient):
    def __init__(self, *, required_marker: str) -> None:
        super().__init__(scenario="valid")
        self.required_marker = required_marker

    def complete(self, **kwargs: Any) -> str:
        serialized = json.dumps(kwargs.get("conversation_context") or {}, ensure_ascii=False)
        if self.required_marker not in serialized:
            raise AssertionError("conversation history was not injected")
        return super().complete(**kwargs)
