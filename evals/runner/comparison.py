"""Prompt A/B comparison with strict control-variable isolation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONTROL_VARIABLES = (
    "git_commit_sha",
    "git_worktree_dirty",
    "git_diff_sha256",
    "dataset_version",
    "corpus_hash",
    "chunking_config_hash",
    "response_envelope_sha256",
    "embedding_model",
    "retrieval_backend",
    "retrieval_limit",
    "generation_context_k",
    "llm_model",
    "temperature",
    "max_output_tokens",
    "seed",
    "repeat_count",
    "metric_cutoffs",
    "split",
    "judge_model",
    "judge_prompt_sha256",
    "conversation_mode",
    "long_term_memory_config",
    "retrieval_config_hash",
)


class ComparisonConfigurationError(ValueError):
    pass


def compare_summaries(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    left = baseline.get("configuration", {})
    right = candidate.get("configuration", {})
    mismatches = [key for key in CONTROL_VARIABLES if key in left or key in right if left.get(key) != right.get(key)]
    if mismatches:
        raise ComparisonConfigurationError("control variables differ: " + ", ".join(mismatches))
    metric_paths = (
        ("retrieval", "document_hit_at_5"),
        ("retrieval", "evidence_recall_at_5"),
        ("retrieval", "all_evidence_hit_at_5"),
        ("answer_quality", "citation_reference_validity_rate"),
        ("answer_quality", "citation_support_rate"),
        ("answer_quality", "unsupported_answer_rate"),
        ("answer_quality", "correct_abstention_rate"),
        ("answer_quality", "format_adherence_rate"),
    )
    metrics: dict[str, dict[str, float | None]] = {}
    for section, name in metric_paths:
        baseline_value = baseline.get(section, {}).get(name)
        candidate_value = candidate.get(section, {}).get(name)
        delta = (
            candidate_value - baseline_value
            if isinstance(baseline_value, (int, float)) and isinstance(candidate_value, (int, float))
            else None
        )
        metrics[name] = {"baseline": baseline_value, "candidate": candidate_value, "delta": delta}
    baseline_cases = baseline.get("case_outcomes", {})
    candidate_cases = candidate.get("case_outcomes", {})
    gates = _acceptance_gates(baseline, candidate)
    statuses = {gate["status"] for gate in gates.values()}
    recommendation = (
        "reject" if "fail" in statuses
        else "manual_review_required" if "not_evaluable" in statuses
        else "accept"
    )
    return {
        "control_variables": {key: left.get(key) for key in CONTROL_VARIABLES if key in left},
        "metrics": metrics,
        "fixed_failures": sorted(
            case for case, status in baseline_cases.items()
            if _case_failed(status) and not _case_failed(candidate_cases.get(case))
        ),
        "new_failures": sorted(
            case for case, status in candidate_cases.items()
            if _case_failed(status) and not _case_failed(baseline_cases.get(case))
        ),
        "acceptance_gates": gates,
        "recommendation": recommendation,
    }


def _case_failed(status: Any) -> bool:
    if status is None:
        return False
    if isinstance(status, list):
        return any(item != "completed" for item in status)
    return status != "completed"


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _gate(condition: bool | None, detail: str) -> dict[str, str]:
    return {
        "status": "not_evaluable" if condition is None else ("pass" if condition else "fail"),
        "detail": detail,
    }


def _acceptance_gates(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, dict[str, str]]:
    left_quality = baseline.get("answer_quality", {})
    right_quality = candidate.get("answer_quality", {})
    left_retrieval = baseline.get("retrieval", {})
    right_retrieval = candidate.get("retrieval", {})
    left_latency = baseline.get("latency", {})
    right_latency = candidate.get("latency", {})

    baseline_format = _number(left_quality.get("format_adherence_rate"))
    candidate_format = _number(right_quality.get("format_adherence_rate"))
    format_ok = None if baseline_format is None or candidate_format is None else (
        candidate_format >= 0.95 or (candidate_format - baseline_format >= 0.10 and candidate_format >= 0.90)
    )

    def no_drop(section_left: dict[str, Any], section_right: dict[str, Any], key: str, allowance: float) -> bool | None:
        left_value = _number(section_left.get(key))
        right_value = _number(section_right.get(key))
        return None if left_value is None or right_value is None else right_value >= left_value - allowance

    unsupported_left = _number(left_quality.get("unsupported_answer_rate"))
    unsupported_right = _number(right_quality.get("unsupported_answer_rate"))
    unsupported_ok = None if unsupported_left is None or unsupported_right is None else unsupported_right <= unsupported_left

    retrieval_keys = ("document_hit_at_5", "evidence_recall_at_5", "all_evidence_hit_at_5")
    retrieval_values = [
        (_number(left_retrieval.get(key)), _number(right_retrieval.get(key))) for key in retrieval_keys
    ]
    retrieval_ok = None if any(left is None or right is None for left, right in retrieval_values) else all(
        left == right for left, right in retrieval_values
    )

    answer_mean_left = _number(left_latency.get("answer_ms", {}).get("mean"))
    answer_mean_right = _number(right_latency.get("answer_ms", {}).get("mean"))
    end_p95_left = _number(left_latency.get("end_to_end_ms", {}).get("p95"))
    end_p95_right = _number(right_latency.get("end_to_end_ms", {}).get("p95"))
    latency_ok = None if None in (answer_mean_left, answer_mean_right, end_p95_left, end_p95_right) else (
        answer_mean_right <= answer_mean_left * 1.15 and end_p95_right <= end_p95_left * 1.20
    )

    return {
        "format": _gate(format_ok, "format >=95%, or >=10pp improvement with final >=90%"),
        "citation_reference": _gate(
            no_drop(left_quality, right_quality, "citation_reference_validity_rate", 0.02),
            "citation reference validity drop <=2pp",
        ),
        "citation_support": _gate(
            no_drop(left_quality, right_quality, "citation_support_rate", 0.02),
            "citation semantic support drop <=2pp; requires Judge or human denominator",
        ),
        "unsupported_answers": _gate(unsupported_ok, "unsupported answer rate must not increase"),
        "prompt_only_retrieval": _gate(retrieval_ok, "prompt-only retrieval metrics must be identical"),
        "latency": _gate(latency_ok, "mean answer <=15% and P95 end-to-end <=20% regression"),
    }


def write_comparison(comparison: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    rows = ["| Metric | Baseline | Candidate | Delta |", "|---|---:|---:|---:|"]
    for name, values in comparison["metrics"].items():
        rows.append(f"| {name} | {values['baseline']} | {values['candidate']} | {values['delta']} |")
    output.write_text(
        "# Prompt Evaluation Comparison\n\n"
        + "\n".join(rows)
        + "\n\n## Fixed Failures\n\n"
        + ("\n".join(f"- {item}" for item in comparison["fixed_failures"]) or "- None")
        + "\n\n## New Failures\n\n"
        + ("\n".join(f"- {item}" for item in comparison["new_failures"]) or "- None")
        + "\n\n## Acceptance Gates\n\n"
        + "\n".join(
            f"- {name}: **{gate['status']}** — {gate['detail']}"
            for name, gate in comparison["acceptance_gates"].items()
        )
        + f"\n\n## Recommendation\n\n**{comparison['recommendation']}**"
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
