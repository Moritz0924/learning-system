from __future__ import annotations

import json
from pathlib import Path

from evals.models import EvaluationRunResult, LearningQaEvaluationCase


def _case() -> LearningQaEvaluationCase:
    return LearningQaEvaluationCase.model_validate({
        "case_id": "case-1", "dataset_version": "v1", "split": "development",
        "category": "single_source", "difficulty": "easy", "question": "q",
        "conversation_history": [], "gold_answer_points": ["a"], "gold_document_ids": ["d1"],
        "gold_evidence_spans": [{"evidence_id": "e1", "document_id": "d1", "text": "evidence"}],
        "is_answerable": True, "expected_behavior": "answer_with_citation",
        "format_contract": {"type": "strict_json", "require_citations": True},
    })


def _run() -> EvaluationRunResult:
    return EvaluationRunResult.model_validate({
        "run_id": "run-1", "run_mode": "mock_smoke", "quality_metrics_are_representative": False,
        "prompt_variant": "candidate", "prompt_sha256": "a" * 64, "split": "development", "repeat_count": 1,
        "dataset_version": "v1", "corpus_hash": "b" * 64, "chunking_config_hash": "c" * 64,
        "model": "mock", "results": [{
            "run_id": "run-1", "case_id": "case-1", "dataset_version": "v1",
            "prompt_variant": "candidate", "prompt_sha256": "a" * 64, "model": "mock",
            "retrieval_config_hash": "r" * 64,
            "execution": {"status": "completed", "retrieval_attempt_count": 1, "llm_attempt_count": 1, "judge_attempt_count": 0},
            "retrieval": {
                "retrieved_chunk_ids": ["c1"], "retrieved_document_ids": ["d1"],
                "retrieval_scores": [{"raw_value": 0.9, "score_kind": "cosine_similarity", "higher_is_better": True}],
                "document_hit_at": {1: 1, 3: 1, 5: 1}, "document_recall_at": {1: 1, 3: 1, 5: 1},
                "chunk_hit_at": {1: 1, 3: 1, 5: 1}, "evidence_recall_at": {1: 1, 3: 1, 5: 1},
                "all_evidence_hit_at": {1: 1, 3: 1, 5: 1}, "retrieval_latency_ms": 4,
                "embedding_latency_ms": 1, "vector_search_latency_ms": 2,
            },
            "answer": {
                "raw_output": "{}", "answer_text": "answer", "cited_chunk_ids": ["c1"], "cited_document_ids": ["d1"],
                "citation_count": 1, "valid_reference_count": 1, "invalid_reference_count": 0,
                "citation_reference_validity_rate": 1, "citation_support_rate": None,
                "citation_semantically_graded_count": 0, "contains_unsupported_claim": None,
                "correctly_abstained": None, "format_followed": True, "json_parse_success": True,
                "required_sections_present": True, "citation_format_valid": True,
                "answer_latency_ms": 2,
                "end_to_end_latency_ms": 7,
            },
            "grader_mode": "automatic",
        }],
    })


def test_report_writer_emits_auditable_artifact_set_and_null_semantic_metrics(tmp_path: Path) -> None:
    from evals.runner.report_writer import write_evaluation_report

    output = write_evaluation_report(
        _run(),
        cases_by_id={"case-1": _case()},
        output_root=tmp_path,
        config={"llm_model": "mock", "retrieval_limit": 5, "generation_context_k": 5},
    )

    expected = {
        "run.json", "config.json", "cases.jsonl", "retrieval-results.csv", "answer-results.csv",
        "summary.json", "summary.md", "failed-cases.jsonl", "human-review.csv",
    }
    assert {path.name for path in output.iterdir()} == expected
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["answer_quality"]["citation_support_rate"] is None
    assert summary["answer_quality"]["unsupported_answer_rate"] is None
    assert summary["answer_quality"]["format_adherence_rate"] == 1.0
    assert summary["answer_quality"]["json_parse_success_rate"] == 1.0
    assert summary["answer_quality"]["citation_format_success_rate"] == 1.0
    assert summary["answer_quality"]["forbidden_field_rate"] == 0.0
    assert "embedding_ms" in summary["latency"]
    assert "retrieval_postprocess_ms" in summary["latency"]
    assert "llm_request_ms" in summary["latency"]
    assert "llm_parse_ms" in summary["latency"]
    assert "not representative" in (output / "summary.md").read_text(encoding="utf-8").lower()


def test_prompt_comparison_rejects_control_variable_mismatch_and_writes_json(tmp_path: Path) -> None:
    from evals.runner.comparison import ComparisonConfigurationError, compare_summaries, write_comparison

    baseline = {
        "configuration": {"git_commit_sha": "g", "dataset_version": "v1", "corpus_hash": "c", "llm_model": "m", "retrieval_limit": 5},
        "retrieval": {"document_hit_at_5": 0.8},
        "answer_quality": {"format_adherence_rate": 0.8},
        "latency": {"end_to_end_ms": {"p95": 10}},
        "case_outcomes": {"case-1": "completed"},
    }
    candidate = json.loads(json.dumps(baseline))
    candidate["answer_quality"]["format_adherence_rate"] = 0.95

    comparison = compare_summaries(baseline, candidate)
    output = tmp_path / "comparison.md"
    write_comparison(comparison, output)
    assert output.exists()
    assert output.with_suffix(".json").exists()
    assert comparison["recommendation"] == "manual_review_required"
    assert "Acceptance Gates" in output.read_text(encoding="utf-8")

    candidate["configuration"]["llm_model"] = "different"
    import pytest
    with pytest.raises(ComparisonConfigurationError, match="llm_model"):
        compare_summaries(baseline, candidate)


def test_human_review_includes_all_test_failures_and_twenty_percent_completed_sample() -> None:
    from evals.runner.report_writer import human_review_case_ids

    base = _run().results[0]
    results = []
    cases = {}
    for index in range(10):
        case_id = f"test-{index}"
        results.append(base.model_copy(update={"case_id": case_id}))
        cases[case_id] = _case().model_copy(update={"case_id": case_id, "split": "test"})
    failed = base.model_copy(deep=True, update={
        "case_id": "test-failed",
        "execution": base.execution.model_copy(update={"status": "parse_error"}),
    })
    results.append(failed)
    cases["test-failed"] = _case().model_copy(update={"case_id": "test-failed", "split": "test"})
    quality_failed = base.model_copy(deep=True, update={
        "case_id": "test-quality-failed",
        "answer": base.answer.model_copy(update={"format_followed": False}),
    })
    results.append(quality_failed)
    cases["test-quality-failed"] = _case().model_copy(update={"case_id": "test-quality-failed", "split": "test"})

    selected = human_review_case_ids(results, cases_by_id=cases, run_id="stable-run")

    assert "test-failed" in selected
    assert "test-quality-failed" in selected
    assert len(selected - {"test-failed", "test-quality-failed"}) == 2


def test_summary_preserves_every_repeat_outcome() -> None:
    from evals.runner.report_writer import build_summary

    run = _run()
    failed_repeat = run.results[0].model_copy(deep=True, update={
        "execution": run.results[0].execution.model_copy(update={"status": "llm_error"}),
    })
    repeated = run.model_copy(update={"repeat_count": 2, "results": [run.results[0], failed_repeat]})

    summary = build_summary(
        repeated,
        cases_by_id={"case-1": _case()},
        config={"llm_model": "mock"},
    )

    assert summary["case_outcomes"]["case-1"] == ["completed", "llm_error"]
