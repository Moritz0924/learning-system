"""Case-level orchestration for the real tutor engine and evaluation adapters."""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns
from typing import Callable
from uuid import uuid4

from adaptive_tutor.phase2.schemas import TutorRunRequest
from adaptive_tutor.phase2.telemetry import TimedLlmResult, TimedRetrievalResult
from backend.app.services.llm_gateway import EvaluationProviderError
from evals.adapters.rag_adapter import EvaluationRetrievalError
from evals.graders.citation_grader import (
    grade_citation_references,
    semantic_citation_grade,
)
from evals.graders.format_grader import grade_format
from evals.graders.grounding_grader import grade_grounding
from evals.graders.retrieval_grader import grade_retrieval
from evals.models import (
    AnswerEvaluationResult,
    EvaluationCaseResult,
    EvaluationExecutionStatus,
    EvaluationRunResult,
    GoldChunkMap,
    LearningQaEvaluationCase,
    PromptVariant,
    RetrievalEvaluationResult,
    WarmupResult,
)
from evals.runner.corpus_seed import EVALUATION_GOAL_ID, EVALUATION_USER_ID


@dataclass(frozen=True)
class EngineExecutionContext:
    engine: object
    retrieval_trace: Callable[[], TimedRetrievalResult | None]
    llm_trace: Callable[[], TimedLlmResult | None]


EngineBuilder = Callable[[LearningQaEvaluationCase, PromptVariant], EngineExecutionContext]


class EvaluationRunner:
    def __init__(
        self,
        *,
        engine_builder: EngineBuilder,
        gold_map: GoldChunkMap,
        corpus_document_ids: set[str],
        metric_cutoffs: list[int],
        retrieval_config_hash: str,
        run_mode: str,
        persistent_conversation: bool = False,
        judge: object | None = None,
        run_id: str | None = None,
    ) -> None:
        self.engine_builder = engine_builder
        self.gold_map = gold_map
        self.corpus_document_ids = corpus_document_ids
        self.metric_cutoffs = sorted(set(metric_cutoffs))
        self.retrieval_config_hash = retrieval_config_hash
        self.run_mode = run_mode
        self.persistent_conversation = persistent_conversation
        self.judge = judge
        self.run_id = run_id or f"eval-{uuid4()}"

    def run_dataset(
        self,
        dataset: list[LearningQaEvaluationCase],
        *,
        prompt_variant: PromptVariant,
        split: str | None,
        repeat_count: int,
        warmup_count: int = 3,
    ) -> EvaluationRunResult:
        if repeat_count < 1:
            raise ValueError("repeat_count must be positive")
        selected = [
            case
            for case in dataset
            if split in {None, "all"} or case.split == split
        ]
        warmup: WarmupResult | None = None
        if warmup_count:
            warmup_cases = [case for case in selected if case.category != "multi_turn"][:warmup_count]
            active_judge = self.judge
            self.judge = None
            try:
                warmup_results = [
                    self.run_case(case, prompt_variant=prompt_variant, repeat_index=-(index + 1))
                    for index, case in enumerate(warmup_cases)
                ]
            finally:
                self.judge = active_judge
            succeeded = len(warmup_results) == warmup_count and all(
                result.execution.status == "completed" for result in warmup_results
            )
            warmup = WarmupResult(
                case_ids=[case.case_id for case in warmup_cases],
                succeeded=succeeded,
                error_message=None if succeeded else "one or more warmup cases failed",
            )

        results = [
            self.run_case(case, prompt_variant=prompt_variant, repeat_index=repeat_index)
            for repeat_index in range(repeat_count)
            for case in selected
        ]
        first_model = next((result.model for result in results if result.model != "unavailable"), None)
        return EvaluationRunResult(
            run_id=self.run_id,
            run_mode=self.run_mode,
            quality_metrics_are_representative=self.run_mode == "remote",
            prompt_variant=prompt_variant.name,
            prompt_sha256=prompt_variant.sha256,
            split=split,
            repeat_count=repeat_count,
            results=results,
            warmup=warmup,
            dataset_version=selected[0].dataset_version if selected else self.gold_map.dataset_version,
            corpus_hash=self.gold_map.corpus_hash,
            chunking_config_hash=self.gold_map.chunking_config_hash,
            model=first_model,
            metric_cutoffs=self.metric_cutoffs,
        )

    def run_case(
        self,
        case: LearningQaEvaluationCase,
        *,
        prompt_variant: PromptVariant,
        repeat_index: int,
    ) -> EvaluationCaseResult:
        started = perf_counter_ns()
        if case.category == "multi_turn" and not self.persistent_conversation:
            return self._skipped_result(case, prompt_variant, repeat_index, started)

        context = self.engine_builder(case, prompt_variant)
        request = TutorRunRequest(
            trigger_type="chat",
            user_id=EVALUATION_USER_ID,
            goal_id=EVALUATION_GOAL_ID,
            thread_id=f"{self.run_id}:{case.case_id}:{repeat_index}",
            user_message=case.question,
        )
        try:
            engine_result = context.engine.run(request)
        except EvaluationRetrievalError as exc:
            return self._retrieval_error_result(case, prompt_variant, repeat_index, exc.trace, started)
        except EvaluationProviderError as exc:
            trace = context.retrieval_trace()
            return self._llm_error_result(case, prompt_variant, repeat_index, trace, exc, started)

        retrieval_trace = context.retrieval_trace()
        if retrieval_trace is None:
            raise RuntimeError("evaluation engine completed without a retrieval trace")
        llm_trace = context.llm_trace()
        if llm_trace is None:
            raise RuntimeError("evaluation engine completed without an LLM trace")

        raw_output = engine_result.final_answer
        format_grade = grade_format(
            raw_output,
            contract=case.format_contract,
            is_answerable=case.is_answerable,
        )
        references = grade_citation_references(
            format_grade.parsed_citations,
            retrieved_chunks=retrieval_trace.chunks,
            corpus_document_ids=self.corpus_document_ids,
            require_citations=case.format_contract.require_citations,
        )

        judge_outcome = None
        if self.judge is not None and format_grade.parsed_answer is not None:
            cited_chunk_ids = {item.get("chunk_id") for item in format_grade.parsed_citations}
            judge_outcome = self.judge.grade(
                question=case.question,
                answer=format_grade.parsed_answer,
                citations=format_grade.parsed_citations,
                evidence=[
                    {
                        "chunk_id": chunk.chunk_id,
                        "document_id": chunk.document_id,
                        "content": chunk.content,
                    }
                    for chunk in retrieval_trace.chunks
                    if chunk.chunk_id in cited_chunk_ids
                ],
                gold_evidence=[span.model_dump() for span in case.gold_evidence_spans],
                gold_answer_points=case.gold_answer_points,
            )
        verdict = judge_outcome.verdict if judge_outcome is not None else None
        judge_error = bool(judge_outcome is not None and judge_outcome.error_code)
        semantic = semantic_citation_grade(
            verdict,
            citation_count=len(format_grade.parsed_citations),
            judge_error=judge_error,
        )
        grounding = grade_grounding(case, judge_verdict=verdict, judge_error=judge_error)
        status = "parse_error" if not format_grade.json_parse_success else ("judge_error" if judge_error else "completed")

        retrieval = grade_retrieval(
            case,
            self.gold_map.cases.get(case.case_id),
            retrieval_trace,
            cutoffs=self.metric_cutoffs,
        )
        elapsed_ms = (perf_counter_ns() - started) / 1_000_000.0
        answer = AnswerEvaluationResult(
            raw_output=raw_output,
            answer_text=format_grade.parsed_answer,
            cited_chunk_ids=[item.get("chunk_id", "") for item in format_grade.parsed_citations],
            cited_document_ids=[item.get("document_id", "") for item in format_grade.parsed_citations],
            citation_count=len(format_grade.parsed_citations),
            valid_reference_count=references.valid_reference_count,
            invalid_reference_count=references.invalid_reference_count,
            citation_reference_validity_rate=references.citation_reference_validity_rate,
            citation_support_rate=semantic.citation_support_rate,
            citation_semantically_graded_count=semantic.semantically_graded_count,
            contains_unsupported_claim=grounding.contains_unsupported_claim,
            correctly_abstained=grounding.correctly_abstained,
            format_followed=format_grade.format_followed,
            json_parse_success=format_grade.json_parse_success,
            required_sections_present=format_grade.required_sections_present,
            citation_format_valid=format_grade.citation_format_valid,
            abstention_format_correct=format_grade.abstention_format_correct,
            forbidden_field_detected=format_grade.forbidden_field_detected,
            llm_request_latency_ms=llm_trace.request_latency_ms,
            llm_parse_latency_ms=llm_trace.parse_latency_ms,
            answer_latency_ms=llm_trace.total_latency_ms,
            end_to_end_latency_ms=elapsed_ms,
        )
        return EvaluationCaseResult(
            run_id=self.run_id,
            case_id=case.case_id,
            repeat_index=repeat_index,
            dataset_version=case.dataset_version,
            prompt_variant=prompt_variant.name,
            prompt_sha256=prompt_variant.sha256,
            model=llm_trace.model,
            retrieval_config_hash=self.retrieval_config_hash,
            execution=EvaluationExecutionStatus(
                status=status,
                retrieval_attempt_count=1,
                llm_attempt_count=llm_trace.retry_count + 1,
                judge_attempt_count=judge_outcome.attempt_count if judge_outcome else 0,
                degraded_mode_used=llm_trace.mode in {"offline", "degraded"},
                error_code=(judge_outcome.error_code if judge_error else ("response_parse_error" if status == "parse_error" else None)),
            ),
            retrieval=retrieval,
            answer=answer,
            grader_mode="llm_judge" if verdict is not None else "automatic",
            judge_reason=judge_outcome.reason if judge_outcome else None,
            judge_result=verdict,
        )

    def _skipped_result(self, case, prompt, repeat_index, started) -> EvaluationCaseResult:
        return self._terminal_result(
            case,
            prompt,
            repeat_index=repeat_index,
            status="skipped_dependency",
            error_code="persistent_conversation_unavailable",
            retrieval_trace=None,
            llm_error=None,
            started=started,
        )

    def _retrieval_error_result(self, case, prompt, repeat_index, trace, started) -> EvaluationCaseResult:
        return self._terminal_result(
            case,
            prompt,
            repeat_index=repeat_index,
            status="retrieval_error",
            error_code=trace.error_code,
            retrieval_trace=trace,
            llm_error=None,
            started=started,
        )

    def _llm_error_result(self, case, prompt, repeat_index, trace, error, started) -> EvaluationCaseResult:
        return self._terminal_result(
            case,
            prompt,
            repeat_index=repeat_index,
            status="llm_error",
            error_code=error.error_code,
            retrieval_trace=trace,
            llm_error=error,
            started=started,
        )

    def _terminal_result(
        self,
        case: LearningQaEvaluationCase,
        prompt: PromptVariant,
        *,
        repeat_index: int,
        status: str,
        error_code: str | None,
        retrieval_trace: TimedRetrievalResult | None,
        llm_error: EvaluationProviderError | None,
        started: int,
    ) -> EvaluationCaseResult:
        if retrieval_trace is None:
            retrieval = _empty_retrieval(case, self.metric_cutoffs)
            retrieval_attempts = 0
        else:
            retrieval = grade_retrieval(
                case,
                self.gold_map.cases.get(case.case_id),
                retrieval_trace,
                cutoffs=self.metric_cutoffs,
            )
            retrieval_attempts = 1
        elapsed_ms = (perf_counter_ns() - started) / 1_000_000.0
        answer_latency = llm_error.total_latency_ms if llm_error else 0.0
        return EvaluationCaseResult(
            run_id=self.run_id,
            case_id=case.case_id,
            repeat_index=repeat_index,
            dataset_version=case.dataset_version,
            prompt_variant=prompt.name,
            prompt_sha256=prompt.sha256,
            model="unavailable",
            retrieval_config_hash=self.retrieval_config_hash,
            execution=EvaluationExecutionStatus(
                status=status,
                retrieval_attempt_count=retrieval_attempts,
                llm_attempt_count=(llm_error.retry_count + 1) if llm_error else 0,
                judge_attempt_count=0,
                degraded_mode_used=False,
                error_code=error_code,
            ),
            retrieval=retrieval,
            answer=AnswerEvaluationResult(
                raw_output="",
                answer_text=None,
                cited_chunk_ids=[],
                cited_document_ids=[],
                citation_count=0,
                valid_reference_count=0,
                invalid_reference_count=0,
                citation_reference_validity_rate=None,
                citation_support_rate=None,
                citation_semantically_graded_count=0,
                contains_unsupported_claim=None,
                correctly_abstained=None,
                format_followed=False,
                answer_latency_ms=answer_latency,
                end_to_end_latency_ms=elapsed_ms,
            ),
            grader_mode="automatic",
        )


def _empty_retrieval(case: LearningQaEvaluationCase, cutoffs: list[int]) -> RetrievalEvaluationResult:
    return RetrievalEvaluationResult(
        retrieved_chunk_ids=[],
        retrieved_document_ids=[],
        retrieval_scores=[],
        document_hit_at={cutoff: 0.0 for cutoff in cutoffs},
        document_recall_at=({cutoff: 0.0 for cutoff in cutoffs} if case.is_answerable else {}),
        chunk_hit_at={cutoff: 0.0 for cutoff in cutoffs},
        evidence_recall_at=({cutoff: 0.0 for cutoff in cutoffs} if case.is_answerable else {}),
        all_evidence_hit_at=({cutoff: 0.0 for cutoff in cutoffs} if case.is_answerable else {}),
        retrieval_latency_ms=0.0,
    )
