from __future__ import annotations

import hashlib

from adaptive_tutor.phase2.schemas import RetrievedChunk, TutorRunResult
from adaptive_tutor.phase2.telemetry import RetrievalScore, TimedLlmResult, TimedRetrievalResult
from backend.app.services.llm_gateway import EvaluationProviderError
from evals.adapters.rag_adapter import EvaluationRetrievalError
from evals.models import GoldChunkMap, GoldChunkMapCase, GoldEvidenceGroup, LearningQaEvaluationCase, PromptVariant


def _case(*, category: str = "single_source") -> LearningQaEvaluationCase:
    answerable = category != "unanswerable"
    return LearningQaEvaluationCase.model_validate({
        "case_id": f"case-{category}",
        "dataset_version": "v1",
        "split": "development",
        "category": category,
        "difficulty": "easy",
        "question": "question",
        "conversation_history": [],
        "gold_answer_points": ["answer"] if answerable else [],
        "gold_document_ids": ["doc-1"] if answerable else [],
        "gold_evidence_spans": ([{"evidence_id": "ev-1", "document_id": "doc-1", "text": "evidence"}] if answerable else []),
        "is_answerable": answerable,
        "expected_behavior": "answer_with_citation" if answerable else "abstain",
        "format_contract": {"type": "strict_json", "require_citations": answerable},
    })


def _trace() -> TimedRetrievalResult:
    chunk = RetrievedChunk(
        chunk_id="chunk-1", document_id="doc-1", content="evidence",
        citation_label="source", trusted_level=3,
    )
    return TimedRetrievalResult(
        chunks=[chunk],
        scores=[RetrievalScore(raw_value=0.9, score_kind="cosine_similarity", higher_is_better=True)],
        embedding_latency_ms=1, vector_search_latency_ms=2, postprocess_latency_ms=1,
        total_latency_ms=4, backend="local_json_embedding", top_k=5, status="grounded",
    )


def _gold() -> GoldChunkMap:
    return GoldChunkMap(
        dataset_version="v1",
        corpus_hash="c" * 64,
        chunking_config_hash="d" * 64,
        cases={
            "case-single_source": GoldChunkMapCase(evidence_groups=[
                GoldEvidenceGroup(evidence_id="ev-1", document_id="doc-1", acceptable_chunk_ids={"chunk-1"})
            ])
        },
    )


def _prompt() -> PromptVariant:
    content = "prompt"
    return PromptVariant(name="candidate", content=content, sha256=hashlib.sha256(content.encode()).hexdigest())


def test_runner_grades_successful_case_from_engine_and_adapter_traces() -> None:
    from evals.runner.evaluation_runner import EngineExecutionContext, EvaluationRunner

    class Engine:
        def run(self, request):
            return TutorRunResult(
                route="teaching",
                final_answer='{"answer":"answer","citations":[{"chunk_id":"chunk-1","document_id":"doc-1"}]}',
            )

    context = EngineExecutionContext(
        engine=Engine(),
        retrieval_trace=lambda: _trace(),
        llm_trace=lambda: TimedLlmResult(
            text="raw", model="mock", mode="mock", request_latency_ms=2,
            parse_latency_ms=0, total_latency_ms=2, retry_count=0,
        ),
    )
    runner = EvaluationRunner(
        engine_builder=lambda case, prompt: context,
        gold_map=_gold(),
        corpus_document_ids={"doc-1"},
        metric_cutoffs=[1, 3, 5],
        retrieval_config_hash="r" * 64,
        run_mode="mock_smoke",
    )

    result = runner.run_case(_case(), prompt_variant=_prompt(), repeat_index=0)

    assert result.execution.status == "completed"
    assert result.retrieval.evidence_recall_at[1] == 1.0
    assert result.answer.citation_reference_validity_rate == 1.0
    assert result.answer.citation_support_rate is None
    assert result.answer.contains_unsupported_claim is None
    assert result.retrieval.retrieval_postprocess_latency_ms >= 0
    assert result.answer.llm_request_latency_ms >= 0
    assert result.answer.llm_parse_latency_ms >= 0


def test_runner_marks_parse_error_but_preserves_case_result() -> None:
    from evals.runner.evaluation_runner import EngineExecutionContext, EvaluationRunner

    class Engine:
        def run(self, request):
            return TutorRunResult(route="teaching", final_answer="not-json")

    context = EngineExecutionContext(
        engine=Engine(), retrieval_trace=lambda: _trace(),
        llm_trace=lambda: TimedLlmResult(text="not-json", model="mock", mode="mock", request_latency_ms=1, parse_latency_ms=0, total_latency_ms=1, retry_count=0),
    )
    runner = EvaluationRunner(
        engine_builder=lambda case, prompt: context, gold_map=_gold(), corpus_document_ids={"doc-1"},
        metric_cutoffs=[1, 3, 5], retrieval_config_hash="r" * 64, run_mode="mock_smoke",
    )
    result = runner.run_case(_case(), prompt_variant=_prompt(), repeat_index=0)
    assert result.execution.status == "parse_error"
    assert result.answer.raw_output == "not-json"


def test_runner_skips_multiturn_without_building_engine() -> None:
    from evals.runner.evaluation_runner import EvaluationRunner

    runner = EvaluationRunner(
        engine_builder=lambda case, prompt: (_ for _ in ()).throw(AssertionError("not called")),
        gold_map=_gold(), corpus_document_ids={"doc-1"}, metric_cutoffs=[1, 3, 5],
        retrieval_config_hash="r" * 64, run_mode="mock_smoke", persistent_conversation=False,
    )
    result = runner.run_case(_case(category="multi_turn"), prompt_variant=_prompt(), repeat_index=0)
    assert result.execution.status == "skipped_dependency"
    assert result.execution.error_code == "persistent_conversation_unavailable"


def test_retrieval_failure_stops_before_llm_and_is_preserved() -> None:
    from evals.runner.evaluation_runner import EngineExecutionContext, EvaluationRunner

    failed_trace = TimedRetrievalResult(
        chunks=[], scores=[], embedding_latency_ms=None, vector_search_latency_ms=None,
        postprocess_latency_ms=0, total_latency_ms=3, backend="pgvector", top_k=5,
        status="failed", error_code="retrieval_database_error",
    )

    class Engine:
        def run(self, request):
            raise EvaluationRetrievalError(failed_trace)

    context = EngineExecutionContext(
        engine=Engine(), retrieval_trace=lambda: failed_trace,
        llm_trace=lambda: (_ for _ in ()).throw(AssertionError("LLM trace must not be read")),
    )
    runner = EvaluationRunner(
        engine_builder=lambda case, prompt: context, gold_map=_gold(), corpus_document_ids={"doc-1"},
        metric_cutoffs=[1, 3, 5], retrieval_config_hash="r" * 64, run_mode="mock_smoke",
    )
    result = runner.run_case(_case(), prompt_variant=_prompt(), repeat_index=0)
    assert result.execution.status == "retrieval_error"
    assert result.execution.llm_attempt_count == 0


def test_run_dataset_records_warmup_but_excludes_it_from_results() -> None:
    from evals.runner.evaluation_runner import EngineExecutionContext, EvaluationRunner
    from evals.runner.judge import JudgeOutcome
    from evals.models import JudgeVerdict

    calls = 0
    judge_calls = 0

    class Judge:
        def grade(self, **kwargs):
            nonlocal judge_calls
            judge_calls += 1
            return JudgeOutcome(
                verdict=JudgeVerdict(citation_supported=True, citation_support_by_index=[True]),
                error_code=None,
                reason="supported",
                attempt_count=1,
            )

    class Engine:
        def run(self, request):
            nonlocal calls
            calls += 1
            return TutorRunResult(
                route="teaching",
                final_answer='{"answer":"answer","citations":[{"chunk_id":"chunk-1","document_id":"doc-1"}]}',
            )

    context = EngineExecutionContext(
        engine=Engine(), retrieval_trace=lambda: _trace(),
        llm_trace=lambda: TimedLlmResult(text="raw", model="mock", mode="mock", request_latency_ms=1, parse_latency_ms=0, total_latency_ms=1, retry_count=0),
    )
    runner = EvaluationRunner(
        engine_builder=lambda case, prompt: context, gold_map=_gold(), corpus_document_ids={"doc-1"},
        metric_cutoffs=[1, 3, 5], retrieval_config_hash="r" * 64, run_mode="mock_smoke", judge=Judge(),
    )

    run = runner.run_dataset(
        [_case(), _case(category="multi_turn")],
        prompt_variant=_prompt(),
        split="development",
        repeat_count=2,
        warmup_count=1,
    )

    assert len(run.results) == 4
    assert calls == 3  # one discarded warmup plus two single-turn measured calls
    assert judge_calls == 2  # warmup exercises providers but never spends a Judge call
    assert run.warmup is not None and run.warmup.succeeded is True
    assert run.quality_metrics_are_representative is False
