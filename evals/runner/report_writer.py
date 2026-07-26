"""Write auditable JSON, JSONL, CSV, and Markdown evaluation artifacts."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from evals.models import EvaluationCaseResult, EvaluationRunResult, LearningQaEvaluationCase
from evals.runner.metrics import latency_summary, mean_or_none


def build_summary(
    run: EvaluationRunResult,
    *,
    cases_by_id: dict[str, LearningQaEvaluationCase],
    config: dict[str, Any],
) -> dict[str, Any]:
    measured = [result for result in run.results if result.execution.status != "skipped_dependency"]
    answerable = [result for result in measured if cases_by_id[result.case_id].is_answerable]
    unanswerable = [result for result in measured if not cases_by_id[result.case_id].is_answerable]
    retrieval: dict[str, float | None] = {}
    for cutoff in run.metric_cutoffs:
        for name, attr in (
            ("document_hit", "document_hit_at"),
            ("document_recall", "document_recall_at"),
            ("chunk_hit", "chunk_hit_at"),
            ("evidence_recall", "evidence_recall_at"),
            ("all_evidence_hit", "all_evidence_hit_at"),
        ):
            values = [getattr(result.retrieval, attr)[cutoff] for result in answerable if cutoff in getattr(result.retrieval, attr)]
            retrieval[f"{name}_at_{cutoff}"] = mean_or_none(values)
    retrieval["unanswerable_irrelevant_retrieval_rate"] = mean_or_none(
        [float(bool(result.retrieval.retrieved_chunk_ids)) for result in unanswerable]
    )

    citation_total = sum(result.answer.citation_count for result in measured)
    valid_total = sum(result.answer.valid_reference_count for result in measured)
    semantic_count = sum(result.answer.citation_semantically_graded_count for result in measured)
    semantic_weighted = sum(
        (result.answer.citation_support_rate or 0) * result.answer.citation_semantically_graded_count
        for result in measured
    )
    unsupported_values = [
        float(result.answer.contains_unsupported_claim)
        for result in measured
        if result.answer.contains_unsupported_claim is not None
    ]
    abstention_values = [
        float(result.answer.correctly_abstained)
        for result in unanswerable
        if result.answer.correctly_abstained is not None
    ]
    prompt_injection_results = [
        result for result in measured if cases_by_id[result.case_id].category == "prompt_injection"
    ]
    prompt_injection_failures = [
        float(bool(result.answer.contains_unsupported_claim or result.answer.forbidden_field_detected))
        for result in prompt_injection_results
        if result.answer.contains_unsupported_claim is not None or result.answer.forbidden_field_detected
    ]
    answer_quality = {
        "format_adherence_rate": mean_or_none([float(result.answer.format_followed) for result in measured]),
        "json_parse_success_rate": mean_or_none([float(result.answer.json_parse_success) for result in measured]),
        "required_section_rate": mean_or_none([float(result.answer.required_sections_present) for result in measured]),
        "citation_format_success_rate": mean_or_none([float(result.answer.citation_format_valid) for result in measured]),
        "abstention_format_rate": mean_or_none([
            float(result.answer.abstention_format_correct)
            for result in unanswerable
            if result.answer.abstention_format_correct is not None
        ]),
        "forbidden_field_rate": mean_or_none([float(result.answer.forbidden_field_detected) for result in measured]),
        "citation_reference_validity_rate": valid_total / citation_total if citation_total else None,
        "citation_reference_denominator": citation_total,
        "citation_support_rate": semantic_weighted / semantic_count if semantic_count else None,
        "citation_semantic_denominator": semantic_count,
        "unsupported_answer_rate": mean_or_none(unsupported_values),
        "unsupported_answer_denominator": len(unsupported_values),
        "correct_abstention_rate": mean_or_none(abstention_values),
        "correct_abstention_denominator": len(abstention_values),
        "unanswerable_wrong_answer_rate": (
            mean_or_none([1.0 - value for value in abstention_values]) if abstention_values else None
        ),
        "unanswerable_wrong_answer_denominator": len(abstention_values),
        "prompt_injection_failure_rate": mean_or_none(prompt_injection_failures),
        "prompt_injection_failure_denominator": len(prompt_injection_failures),
        "missing_required_citation_rate": mean_or_none([
            float(cases_by_id[result.case_id].format_contract.require_citations and result.answer.citation_count == 0)
            for result in measured
        ]),
    }
    latency = {
        "embedding_ms": latency_summary([
            result.retrieval.embedding_latency_ms
            for result in measured
            if result.retrieval.embedding_latency_ms is not None
        ]),
        "vector_search_ms": latency_summary([
            result.retrieval.vector_search_latency_ms
            for result in measured
            if result.retrieval.vector_search_latency_ms is not None
        ]),
        "retrieval_postprocess_ms": latency_summary([
            result.retrieval.retrieval_postprocess_latency_ms for result in measured
        ]),
        "retrieval_ms": latency_summary([result.retrieval.retrieval_latency_ms for result in measured]),
        "llm_request_ms": latency_summary([result.answer.llm_request_latency_ms for result in measured]),
        "llm_parse_ms": latency_summary([result.answer.llm_parse_latency_ms for result in measured]),
        "answer_ms": latency_summary([result.answer.answer_latency_ms for result in measured]),
        "end_to_end_ms": latency_summary([result.answer.end_to_end_latency_ms for result in measured]),
    }
    configuration = {
        **config,
        "dataset_version": run.dataset_version,
        "corpus_version": run.corpus_version,
        "corpus_hash": run.corpus_hash,
        "chunking_config_hash": run.chunking_config_hash,
        "prompt_variant": run.prompt_variant,
        "prompt_sha256": run.prompt_sha256,
        "response_envelope_sha256": run.response_envelope_sha256,
        "repeat_count": run.repeat_count,
        "metric_cutoffs": run.metric_cutoffs,
    }
    return {
        "run_id": run.run_id,
        "run_mode": run.run_mode,
        "quality_metrics_are_representative": run.quality_metrics_are_representative,
        "configuration": configuration,
        "execution_status": _status_counts(run.results),
        "retrieval": retrieval,
        "answer_quality": answer_quality,
        "latency": latency,
        "case_outcomes": _case_outcomes(run.results, cases_by_id=cases_by_id),
    }


def _status_counts(results: list[EvaluationCaseResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.execution.status] = counts.get(result.execution.status, 0) + 1
    return counts


def _case_outcomes(
    results: list[EvaluationCaseResult],
    *,
    cases_by_id: dict[str, LearningQaEvaluationCase],
) -> dict[str, list[str]]:
    outcomes: dict[str, list[str]] = {}
    for result in results:
        label = (
            result.execution.status
            if result.execution.status != "completed"
            else "quality_failure"
            if _quality_review_required(result, cases_by_id[result.case_id])
            else "completed"
        )
        outcomes.setdefault(result.case_id, []).append(label)
    return outcomes


def write_evaluation_report(
    run: EvaluationRunResult,
    *,
    cases_by_id: dict[str, LearningQaEvaluationCase],
    output_root: Path,
    config: dict[str, Any],
) -> Path:
    run_dir = output_root / run.run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    summary = build_summary(run, cases_by_id=cases_by_id, config=config)
    metadata = run.model_dump(mode="json", exclude={"results"})
    _write_json(run_dir / "run.json", metadata)
    _write_json(run_dir / "config.json", config)
    _write_json(run_dir / "summary.json", summary)
    with (run_dir / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for result in run.results:
            handle.write(result.model_dump_json() + "\n")
    with (run_dir / "failed-cases.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for result in run.results:
            if result.execution.status != "completed":
                handle.write(result.model_dump_json() + "\n")
    _write_retrieval_csv(run_dir / "retrieval-results.csv", run.results)
    _write_answer_csv(run_dir / "answer-results.csv", run.results)
    _write_human_review_csv(
        run_dir / "human-review.csv",
        run.results,
        cases_by_id=cases_by_id,
        run_id=run.run_id,
    )
    (run_dir / "summary.md").write_text(_summary_markdown(summary), encoding="utf-8", newline="\n")
    return run_dir


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _write_retrieval_csv(path: Path, results: list[EvaluationCaseResult]) -> None:
    fields = [
        "case_id", "repeat_index", "status", "retrieved_chunk_ids", "retrieved_document_ids",
        "embedding_latency_ms", "vector_search_latency_ms",
        "retrieval_postprocess_latency_ms", "retrieval_latency_ms",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({
                "case_id": result.case_id,
                "repeat_index": result.repeat_index,
                "status": result.execution.status,
                "retrieved_chunk_ids": "|".join(result.retrieval.retrieved_chunk_ids),
                "retrieved_document_ids": "|".join(result.retrieval.retrieved_document_ids),
                "embedding_latency_ms": result.retrieval.embedding_latency_ms,
                "vector_search_latency_ms": result.retrieval.vector_search_latency_ms,
                "retrieval_postprocess_latency_ms": result.retrieval.retrieval_postprocess_latency_ms,
                "retrieval_latency_ms": result.retrieval.retrieval_latency_ms,
            })


def _write_answer_csv(path: Path, results: list[EvaluationCaseResult]) -> None:
    fields = [
        "case_id", "repeat_index", "status", "format_followed", "json_parse_success",
        "citation_format_valid", "forbidden_field_detected", "citation_count",
        "valid_reference_count", "citation_reference_validity_rate", "citation_support_rate",
        "citation_semantically_graded_count", "contains_unsupported_claim", "correctly_abstained",
        "llm_request_latency_ms", "llm_parse_latency_ms", "answer_latency_ms", "end_to_end_latency_ms",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({field: (
                getattr(result.answer, field) if hasattr(result.answer, field) else getattr(result.execution, field)
            ) for field in fields if field not in {"case_id", "repeat_index"}} | {
                "case_id": result.case_id,
                "repeat_index": result.repeat_index,
            })


def human_review_case_ids(
    results: list[EvaluationCaseResult],
    *,
    cases_by_id: dict[str, LearningQaEvaluationCase],
    run_id: str,
) -> set[str]:
    """Select every failed case plus a reproducible 20% sample of completed Test cases."""
    failed = {result.case_id for result in results if result.execution.status != "completed"}
    quality_failed_test = {
        result.case_id
        for result in results
        if result.execution.status == "completed"
        and result.case_id in cases_by_id
        and cases_by_id[result.case_id].split == "test"
        and _quality_review_required(result, cases_by_id[result.case_id])
    }
    completed_test = {
        result.case_id
        for result in results
        if result.execution.status == "completed"
        and result.case_id in cases_by_id
        and cases_by_id[result.case_id].split == "test"
        and result.case_id not in quality_failed_test
    }
    sample_size = math.ceil(len(completed_test) * 0.2)
    ranked = sorted(
        completed_test,
        key=lambda case_id: hashlib.sha256(f"{run_id}:{case_id}".encode("utf-8")).hexdigest(),
    )
    return failed | quality_failed_test | set(ranked[:sample_size])


def _quality_review_required(
    result: EvaluationCaseResult,
    case: LearningQaEvaluationCase,
) -> bool:
    return bool(
        not result.answer.format_followed
        or result.answer.invalid_reference_count > 0
        or (case.format_contract.require_citations and result.answer.citation_count == 0)
        or (
            result.answer.citation_support_rate is not None
            and result.answer.citation_support_rate < 1.0
        )
        or result.answer.contains_unsupported_claim is True
        or (not case.is_answerable and result.answer.correctly_abstained is False)
    )


def _write_human_review_csv(
    path: Path,
    results: list[EvaluationCaseResult],
    *,
    cases_by_id: dict[str, LearningQaEvaluationCase],
    run_id: str,
) -> None:
    fields = [
        "case_id",
        "repeat_index",
        "review_reason",
        "original_grader_mode",
        "original_judge_reason",
        "original_judge_result",
        "human_override",
        "override_reason",
        "reviewer",
    ]
    selected = human_review_case_ids(results, cases_by_id=cases_by_id, run_id=run_id)
    grouped: dict[str, list[EvaluationCaseResult]] = {}
    for result in results:
        grouped.setdefault(result.case_id, []).append(result)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case_id in sorted(selected):
            candidates = grouped[case_id]
            review_rows = [
                item for item in candidates
                if item.execution.status != "completed"
                or _quality_review_required(item, cases_by_id[case_id])
            ] or [candidates[0]]
            for result in review_rows:
                review_reason = (
                    "execution_failure"
                    if result.execution.status != "completed"
                    else "quality_failure"
                    if _quality_review_required(result, cases_by_id[case_id])
                    else "test_completed_20_percent_sample"
                )
                writer.writerow({
                "case_id": case_id,
                "repeat_index": result.repeat_index,
                "review_reason": review_reason,
                "original_grader_mode": (
                    "llm_judge"
                    if result.grader_mode == "human_override" and result.judge_result is not None
                    else result.grader_mode
                ),
                "original_judge_reason": result.judge_reason or "",
                "original_judge_result": (
                    result.judge_result.model_dump_json() if result.judge_result is not None else ""
                ),
                "human_override": (
                    result.human_override_result.model_dump_json()
                    if result.human_override_result is not None
                    else ""
                ),
                "override_reason": result.human_override_reason or "",
                "reviewer": result.human_reviewer or "",
                })


def _summary_markdown(summary: dict[str, Any]) -> str:
    warning = "" if summary["quality_metrics_are_representative"] else "> Mock metrics are not representative of real model quality.\n\n"
    quality = summary["answer_quality"]
    retrieval_rows = ["| Metric | Value |", "|---|---:|"] + [
        f"| {name} | {value} |" for name, value in summary["retrieval"].items()
    ]
    latency_rows = ["| Stage | Mean | P50 | P95 | Min | Max | Std | N |", "|---|---:|---:|---:|---:|---:|---:|---:|"] + [
        (
            f"| {name} | {values['mean']} | {values['p50']} | {values['p95']} | "
            f"{values['min']} | {values['max']} | {values['std']} | {values['count']} |"
        )
        for name, values in summary["latency"].items()
    ]
    return (
        "# Learning QA Evaluation Report\n\n"
        + warning
        + "## Execution\n\n"
        + f"- Run mode: {summary['run_mode']}\n"
        + f"- Status counts: `{json.dumps(summary['execution_status'], ensure_ascii=False)}`\n\n"
        + "## Retrieval\n\n"
        + "\n".join(retrieval_rows)
        + "\n\n"
        + "## Answer Quality\n\n"
        + f"- Format adherence: {quality['format_adherence_rate']}\n"
        + f"- JSON parse success: {quality['json_parse_success_rate']}\n"
        + f"- Citation reference validity: {quality['citation_reference_validity_rate']}\n"
        + f"- Citation support: {quality['citation_support_rate']} (n={quality['citation_semantic_denominator']})\n"
        + f"- Unsupported answer rate: {quality['unsupported_answer_rate']} (n={quality['unsupported_answer_denominator']})\n"
        + f"- Correct abstention rate: {quality['correct_abstention_rate']} (n={quality['correct_abstention_denominator']})\n\n"
        + "## Latency\n\n"
        + "\n".join(latency_rows)
        + "\n"
    )
