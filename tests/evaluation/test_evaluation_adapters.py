from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from adaptive_tutor.phase2.schemas import RetrievedChunk
from adaptive_tutor.phase2.telemetry import RetrievalScore, TimedLlmResult, TimedRetrievalResult
from evals.models import PromptVariant


def _chunk(index: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"chunk-{index}",
        document_id=f"doc-{index}",
        content=f"content {index}",
        citation_label=f"source {index}",
        trusted_level=3,
    )


def test_rag_adapter_captures_full_trace_and_returns_generation_slice() -> None:
    from evals.adapters.rag_adapter import EvaluationRagAdapter

    class Repository:
        calls = 0

        def retrieve_timed(self, query: str, *, top_k: int, user_id: str | None):
            self.calls += 1
            chunks = [_chunk(index) for index in range(1, 6)]
            return TimedRetrievalResult(
                chunks=chunks,
                scores=[RetrievalScore(raw_value=1 / index, score_kind="cosine_similarity", higher_is_better=True) for index in range(1, 6)],
                embedding_latency_ms=1,
                vector_search_latency_ms=2,
                postprocess_latency_ms=1,
                total_latency_ms=4,
                backend="local_json_embedding",
                top_k=top_k,
                status="grounded",
            )

    repository = Repository()
    adapter = EvaluationRagAdapter(repository, retrieval_limit=5, generation_context_k=2)

    returned = adapter.retrieve("question", top_k=99, user_id="eval-user")

    assert repository.calls == 1
    assert [item.chunk_id for item in returned] == ["chunk-1", "chunk-2"]
    assert adapter.last_trace is not None
    assert len(adapter.last_trace.chunks) == 5


def test_rag_adapter_raises_on_infrastructure_failure() -> None:
    from evals.adapters.rag_adapter import EvaluationRagAdapter, EvaluationRetrievalError

    class Repository:
        def retrieve_timed(self, query: str, *, top_k: int, user_id: str | None):
            return TimedRetrievalResult(
                chunks=[], scores=[], embedding_latency_ms=None, vector_search_latency_ms=None,
                postprocess_latency_ms=0, total_latency_ms=3, backend="pgvector", top_k=top_k,
                status="failed", error_code="retrieval_database_error",
            )

    adapter = EvaluationRagAdapter(Repository(), retrieval_limit=5, generation_context_k=5)
    with pytest.raises(EvaluationRetrievalError) as caught:
        adapter.retrieve("question")
    assert caught.value.trace.error_code == "retrieval_database_error"


def test_v2_rag_adapter_uses_trace_aware_selected_context_not_legacy_timing() -> None:
    from evals.adapters.rag_adapter import EvaluationRagAdapter

    selected = [
        SimpleNamespace(
            chunk_id=f"chunk-selected-{index}",
            document_id=f"doc-{index}",
            content=f"selected content {index}",
            citation_label=f"source {index}",
            source_title=f"doc-{index}.md",
            source_url=None,
            trusted_level=3,
            metadata={"chunk_schema_version": "v2"},
        )
        for index in range(1, 4)
    ]
    result = SimpleNamespace(
        status="grounded",
        error_code=None,
        selected_candidates=tuple(selected),
        trace=SimpleNamespace(
            source_attempts=(
                SimpleNamespace(source="vector", elapsed_ms=1.25),
                SimpleNamespace(source="keyword", elapsed_ms=0.75),
                SimpleNamespace(source="metadata", elapsed_ms=0.25),
            ),
            fusion_elapsed_ms=0.2,
            rerank_elapsed_ms=0.3,
            selection_elapsed_ms=0.1,
        ),
    )

    class Repository:
        request = None

        def retrieve_timed(self, *args, **kwargs):
            raise AssertionError("V2 evaluation must not use legacy vector timing")

        def retrieve_v2(self, request):
            self.request = request
            return result

    repository = Repository()
    adapter = EvaluationRagAdapter(
        repository,
        retrieval_limit=7,
        generation_context_k=2,
        index_schema="v2",
    )

    returned = adapter.retrieve("hybrid question", top_k=99, user_id="eval-user")

    assert repository.request.query == "hybrid question"
    assert repository.request.top_k == 7
    assert repository.request.user_id == "eval-user"
    assert [chunk.chunk_id for chunk in returned] == [
        "chunk-selected-1",
        "chunk-selected-2",
    ]
    assert adapter.last_result is result
    assert [chunk.chunk_id for chunk in adapter.last_trace.chunks] == [
        "chunk-selected-1",
        "chunk-selected-2",
        "chunk-selected-3",
    ]
    assert adapter.last_trace.backend == "hybrid_v2"
    assert adapter.last_trace.postprocess_latency_ms == pytest.approx(0.6)
    assert adapter.last_trace.scores == []


def test_hybrid_v3_rag_adapter_uses_existing_orchestrated_retrieval() -> None:
    from evals.adapters.rag_adapter import EvaluationRagAdapter

    selected = [
        SimpleNamespace(
            chunk_id=f"chunk-selected-{index}",
            document_id=f"doc-{index}",
            content=f"selected content {index}",
            citation_label=f"source {index}",
            source_title=f"doc-{index}.md",
            source_url=None,
            trusted_level=3,
            metadata={"chunk_schema_version": "v3"},
        )
        for index in range(1, 3)
    ]
    result = SimpleNamespace(
        status="grounded",
        error_code=None,
        selected_candidates=tuple(selected),
        trace=SimpleNamespace(
            fusion_elapsed_ms=0.2,
            rerank_elapsed_ms=0.3,
            selection_elapsed_ms=0.1,
        ),
    )

    class Repository:
        request = None

        def retrieve_timed(self, *args, **kwargs):
            raise AssertionError("hybrid-v3 evaluation must not use legacy vector timing")

        def retrieve_v2(self, request):
            self.request = request
            return result

    repository = Repository()
    adapter = EvaluationRagAdapter(
        repository,
        retrieval_limit=7,
        generation_context_k=2,
        index_schema="hybrid-v3",
    )

    returned = adapter.retrieve("hybrid v3 question", top_k=99, user_id="eval-user")

    assert repository.request.query == "hybrid v3 question"
    assert repository.request.top_k == 7
    assert [chunk.chunk_id for chunk in returned] == [
        "chunk-selected-1",
        "chunk-selected-2",
    ]
    assert adapter.last_trace is not None
    assert adapter.last_trace.backend == "hybrid_v2"


def test_llm_adapter_requires_allow_remote_before_gateway_call() -> None:
    from evals.adapters.llm_adapter import EvaluationLlmAdapter
    from backend.app.services.llm_gateway import EvaluationProviderError

    class Gateway:
        calls = 0

        def complete_timed(self, **kwargs):
            self.calls += 1
            raise AssertionError("must not be called")

    gateway = Gateway()
    adapter = EvaluationLlmAdapter(
        gateway,
        PromptVariant(name="candidate", content="instructions", sha256="a" * 64),
        response_envelope="envelope",
        allow_remote=False,
        temperature=0,
        max_output_tokens=256,
        seed=1,
    )

    with pytest.raises(EvaluationProviderError, match="--allow-remote"):
        adapter.complete(role="teacher", prompt="question")
    assert gateway.calls == 0


def test_llm_adapter_captures_trace_without_changing_text() -> None:
    from evals.adapters.llm_adapter import EvaluationLlmAdapter

    class Gateway:
        def complete_timed(self, **kwargs):
            assert kwargs["instruction_prompt"] == "instructions"
            assert kwargs["response_envelope"] == "envelope"
            assert kwargs["strict_remote"] is True
            return TimedLlmResult(
                text="raw output", model="model", mode="remote", request_latency_ms=1,
                parse_latency_ms=0.1, total_latency_ms=1.1, retry_count=0,
            )

    adapter = EvaluationLlmAdapter(
        Gateway(),
        PromptVariant(name="candidate", content="instructions", sha256="a" * 64),
        response_envelope="envelope",
        allow_remote=True,
        temperature=0,
        max_output_tokens=256,
        seed=1,
    )

    assert adapter.complete(role="teacher", prompt="question") == "raw output"
    assert adapter.last_trace is not None
    assert adapter.last_trace.text == "raw output"


def test_mock_client_scenarios_are_explicit_and_traceable() -> None:
    from evals.adapters.mock_clients import MockJsonLlmClient

    valid = MockJsonLlmClient(scenario="valid")
    raw = valid.complete(role="teacher", prompt="q", context=[_chunk(1)])
    parsed = json.loads(raw)
    assert parsed["citations"] == [{"chunk_id": "chunk-1", "document_id": "doc-1"}]
    assert valid.last_trace is not None and valid.last_trace.mode == "mock"

    assert MockJsonLlmClient(scenario="invalid_json").complete(role="teacher", prompt="q") == "not-json"


def test_history_aware_mock_requires_real_conversation_context() -> None:
    from evals.adapters.mock_clients import HistoryAwareMockLlmClient

    client = HistoryAwareMockLlmClient(required_marker="第一轮关键概念")
    with pytest.raises(AssertionError, match="conversation history"):
        client.complete(role="teacher", prompt="follow up", conversation_context={})

    output = client.complete(
        role="teacher",
        prompt="follow up",
        conversation_context={"messages": ["第一轮关键概念"]},
    )
    assert json.loads(output)["answer"]
