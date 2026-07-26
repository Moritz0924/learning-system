from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "evals" / "datasets" / "learning_qa_v1.jsonl"
CORPUS = ROOT / "evals" / "corpus" / "learning_qa_v1"


def test_dry_run_counts_executable_calls_without_remote_access() -> None:
    from evals.runner.cost_estimation import estimate_evaluation_cost
    from evals.runner.dataset_loader import load_and_validate_dataset

    cases = load_and_validate_dataset(DATASET, corpus_dir=CORPUS).cases
    estimate = estimate_evaluation_cost(
        cases,
        split="test",
        repeat_count=3,
        warmup_count=3,
        judge_enabled=True,
        persistent_conversation=False,
        estimated_input_tokens_per_call=1000,
        estimated_output_tokens_per_call=200,
        input_cost_per_million=2.0,
        output_cost_per_million=8.0,
    )

    # Test has 16 cases, one multi-turn dependency skip: 15 * 3 measured + 3 warmup.
    assert estimate.tutor_llm_calls == 48
    assert estimate.judge_calls == 45
    assert estimate.embedding_calls == 48
    assert estimate.max_tutor_provider_attempts == 48
    assert estimate.max_judge_provider_attempts == 45
    assert estimate.skipped_dependency_cases == 1
    assert estimate.remote_execution_enabled is False
    assert estimate.estimated_input_tokens == (48 + 45) * 1000
    assert estimate.estimated_cost is not None and estimate.estimated_cost > 0


def test_prompt_loader_hashes_variant_and_envelope_independently() -> None:
    from evals.runner.prompt_loader import load_prompt_variant, load_response_envelope

    variant = load_prompt_variant(ROOT / "evals" / "prompts" / "tutor_candidate_v2.txt")
    envelope = load_response_envelope(ROOT / "evals" / "prompts" / "evaluation_response_envelope_v1.txt")

    assert variant.name == "tutor-candidate-v2"
    assert len(variant.sha256) == 64
    assert envelope.content
    assert len(envelope.sha256) == 64
    assert variant.sha256 != envelope.sha256


def test_cost_budget_uses_maximum_provider_attempts_when_retries_are_allowed() -> None:
    from evals.runner.cost_estimation import estimate_evaluation_cost
    from evals.runner.dataset_loader import load_and_validate_dataset

    cases = load_and_validate_dataset(DATASET, corpus_dir=CORPUS).cases
    estimate = estimate_evaluation_cost(
        cases[:1],
        split=None,
        repeat_count=1,
        warmup_count=0,
        judge_enabled=False,
        persistent_conversation=False,
        estimated_input_tokens_per_call=100,
        estimated_output_tokens_per_call=50,
        input_cost_per_million=1.0,
        output_cost_per_million=1.0,
        tutor_max_retries=1,
    )

    assert estimate.tutor_llm_calls == 1
    assert estimate.max_tutor_provider_attempts == 2
    assert estimate.estimated_input_tokens == 200
    assert estimate.estimated_output_tokens == 100
