"""Side-effect-free call and cost estimation for evaluation dry runs."""
from __future__ import annotations

from dataclasses import dataclass

from evals.models import LearningQaEvaluationCase


@dataclass(frozen=True)
class EvaluationCostEstimate:
    dataset_cases: int
    repeats: int
    warmup_calls: int
    tutor_llm_calls: int
    judge_calls: int
    embedding_calls: int
    max_tutor_provider_attempts: int
    max_judge_provider_attempts: int
    skipped_dependency_cases: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost: float | None
    remote_execution_enabled: bool = False


def estimate_evaluation_cost(
    cases: list[LearningQaEvaluationCase],
    *,
    split: str | None,
    repeat_count: int,
    warmup_count: int,
    judge_enabled: bool,
    persistent_conversation: bool,
    estimated_input_tokens_per_call: int,
    estimated_output_tokens_per_call: int,
    input_cost_per_million: float | None,
    output_cost_per_million: float | None,
    tutor_max_retries: int = 0,
    judge_max_retries: int = 0,
) -> EvaluationCostEstimate:
    selected = [case for case in cases if split in {None, "all"} or case.split == split]
    skipped = sum(case.category == "multi_turn" and not persistent_conversation for case in selected)
    executable = len(selected) - skipped
    measured_tutor_calls = executable * repeat_count
    actual_warmup = min(warmup_count, executable)
    tutor_calls = measured_tutor_calls + actual_warmup
    judge_calls = measured_tutor_calls if judge_enabled else 0
    max_tutor_attempts = tutor_calls * (tutor_max_retries + 1)
    max_judge_attempts = judge_calls * (judge_max_retries + 1)
    max_model_attempts = max_tutor_attempts + max_judge_attempts
    input_tokens = max_model_attempts * estimated_input_tokens_per_call
    output_tokens = max_model_attempts * estimated_output_tokens_per_call
    cost = None
    if input_cost_per_million is not None and output_cost_per_million is not None:
        cost = (
            input_tokens * input_cost_per_million / 1_000_000
            + output_tokens * output_cost_per_million / 1_000_000
        )
    return EvaluationCostEstimate(
        dataset_cases=len(selected),
        repeats=repeat_count,
        warmup_calls=actual_warmup,
        tutor_llm_calls=tutor_calls,
        judge_calls=judge_calls,
        embedding_calls=tutor_calls,
        max_tutor_provider_attempts=max_tutor_attempts,
        max_judge_provider_attempts=max_judge_attempts,
        skipped_dependency_cases=skipped,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        estimated_cost=cost,
        remote_execution_enabled=False,
    )
