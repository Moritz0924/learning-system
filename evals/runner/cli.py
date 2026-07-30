"""Command implementations for the offline evaluation toolchain."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from backend.app.db import Base, enable_sqlite_foreign_keys
from backend.app.services.embeddings import DeterministicEmbeddingClient, build_embedding_client
from backend.app.services.llm_gateway import LLMGatewayClient
from evals.adapters.llm_adapter import EvaluationLlmAdapter
from evals.adapters.mock_clients import MockJsonLlmClient
from evals.models import GoldChunkMap, LearningQaEvaluationCase
from evals.runner.comparison import compare_summaries, write_comparison
from evals.runner.corpus_seed import seed_evaluation_corpus
from evals.runner.corpus_seed_v2 import seed_evaluation_corpus_v2
from evals.runner.cost_estimation import estimate_evaluation_cost
from evals.runner.dataset_loader import load_and_validate_dataset
from evals.runner.engine_factory import EvaluationEngineFactory
from evals.runner.evaluation_config import (
    EvaluationConfig,
    EvaluationSafetyError,
    persistent_conversation_available,
)
from evals.runner.evaluation_runner import EvaluationRunner
from evals.runner.gold_chunk_map import build_gold_chunk_map, gold_chunk_map_json
from evals.runner.gold_chunk_map_v2 import (
    build_gold_chunk_map_v2,
    gold_chunk_map_v2_json,
)
from evals.runner.hashing import canonical_text_sha256
from evals.runner.judge import EvaluationJudge, JudgeConfig
from evals.runner.prompt_loader import load_prompt_variant, load_response_envelope
from evals.runner.report_writer import write_evaluation_report


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = PROJECT_ROOT / "evals" / "corpus" / "learning_qa_v1"
DEFAULT_DATASET = PROJECT_ROOT / "evals" / "datasets" / "learning_qa_v1.jsonl"
DEFAULT_PROMPT = PROJECT_ROOT / "evals" / "prompts" / "tutor_candidate_v2.txt"
DEFAULT_ENVELOPE = PROJECT_ROOT / "evals" / "prompts" / "evaluation_response_envelope_v1.txt"
DEFAULT_JUDGE_PROMPT = PROJECT_ROOT / "evals" / "prompts" / "citation_judge_v1.txt"
DEFAULT_RESULTS = PROJECT_ROOT / "evals" / "results"


def verify_dataset_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    args = parser.parse_args(argv)
    result = load_and_validate_dataset(args.dataset, corpus_dir=args.corpus)
    if result.errors:
        for error in result.errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Dataset valid: {len(result.cases)} cases")
    print(f"Development: {sum(case.split == 'development' for case in result.cases)}")
    print(f"Test: {sum(case.split == 'test' for case in result.cases)}")
    return 0


def build_chunk_map_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-chars", type=int, default=500)
    args = parser.parse_args(argv)
    mapping = build_gold_chunk_map(args.dataset, corpus_dir=args.corpus, max_chars=args.max_chars)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(gold_chunk_map_json(mapping), encoding="utf-8", newline="\n")
    print(args.output.resolve())
    return 0


def build_chunk_map_v2_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    mapping = build_gold_chunk_map_v2(args.dataset, corpus_dir=args.corpus)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        gold_chunk_map_v2_json(mapping),
        encoding="utf-8",
        newline="\n",
    )
    print(args.output.resolve())
    return 0


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--response-envelope", type=Path, default=DEFAULT_ENVELOPE)
    parser.add_argument("--split", choices=("development", "test", "all"), default="development")
    parser.add_argument("--metric-cutoffs", nargs="+", type=int, default=[1, 3, 5])
    parser.add_argument("--retrieval-limit", type=int, default=5)
    parser.add_argument("--generation-context-k", type=int, default=5)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--estimate-cost", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--input-cost-per-million", type=float)
    parser.add_argument("--output-cost-per-million", type=float)
    return parser


def run_evaluation_main(argv: list[str] | None = None) -> int:
    args = _run_parser().parse_args(argv)
    try:
        return _run_evaluation(args)
    except (EvaluationSafetyError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _run_evaluation(args: argparse.Namespace) -> int:
    if args.retrieval_limit < max(args.metric_cutoffs):
        raise ValueError("retrieval_limit must be at least max(metric_cutoffs)")
    if not 1 <= args.generation_context_k <= args.retrieval_limit:
        raise ValueError("generation_context_k must be between 1 and retrieval_limit")
    validation = load_and_validate_dataset(args.dataset, corpus_dir=args.corpus)
    if validation.errors:
        raise ValueError("dataset validation failed: " + validation.errors[0])
    persistent = persistent_conversation_available()
    judge_enabled = all(os.getenv(name) for name in ("JUDGE_LLM_BASE_URL", "JUDGE_LLM_API_KEY", "JUDGE_LLM_MODEL"))
    if args.dry_run:
        estimate = estimate_evaluation_cost(
            validation.cases,
            split=args.split,
            repeat_count=args.repeat,
            warmup_count=3,
            judge_enabled=judge_enabled,
            persistent_conversation=persistent,
            estimated_input_tokens_per_call=2000,
            estimated_output_tokens_per_call=args.max_output_tokens,
            input_cost_per_million=args.input_cost_per_million,
            output_cost_per_million=args.output_cost_per_million,
            tutor_max_retries=max(0, int(os.getenv("LLM_MAX_RETRIES", "1"))),
            judge_max_retries=0,
        )
        _print_estimate(estimate)
        return 0
    if not args.mock and not args.allow_remote:
        raise EvaluationSafetyError("remote evaluation requires --allow-remote")

    prompt = load_prompt_variant(args.prompt)
    envelope = load_response_envelope(args.response_envelope)
    mapping = build_gold_chunk_map(args.dataset, corpus_dir=args.corpus)
    manifest = json.loads((args.corpus / "manifest.json").read_text(encoding="utf-8"))
    corpus_document_ids = {item["document_id"] for item in manifest["documents"]}
    selected = [case for case in validation.cases if args.split == "all" or case.split == args.split]
    scenario_by_case: dict[str, str] = {}

    if args.mock:
        selected, scenario_by_case = _mock_smoke_selection(selected, args.max_cases or 5)
        db_engine = create_engine("sqlite+pysqlite:///:memory:")
        enable_sqlite_foreign_keys(db_engine)
        Base.metadata.create_all(db_engine)
        session = Session(db_engine)
        embedding = DeterministicEmbeddingClient()
        seed_evaluation_corpus(session, corpus_dir=args.corpus, embedding_client=embedding, reset=False)

        def llm_factory(case, variant):
            return MockJsonLlmClient(scenario=scenario_by_case[case.case_id])

        run_mode = "mock_smoke"
        warmup_count = 0
        judge = None
    else:
        config = EvaluationConfig.from_environment(allow_remote=args.allow_remote)
        validate_formal_embedding_backend()
        config.require_remote("llm")
        config.require_remote("embedding")
        database_url = config.require_database_url(require_postgres=True)
        db_engine = create_engine(database_url, pool_pre_ping=True)
        session = Session(db_engine)
        embedding = build_embedding_client()
        gateway = LLMGatewayClient()

        def llm_factory(case, variant):
            return EvaluationLlmAdapter(
                gateway,
                variant,
                response_envelope=envelope.content,
                allow_remote=True,
                temperature=args.temperature,
                max_output_tokens=args.max_output_tokens,
                seed=args.seed,
            )

        run_mode = "remote"
        warmup_count = 3
        judge = build_optional_judge()

    if args.max_cases and not args.mock:
        selected = selected[: args.max_cases]
    factory = EvaluationEngineFactory(
        session=session,
        embedding_client=embedding,
        llm_client_factory=llm_factory,
        retrieval_limit=args.retrieval_limit,
        generation_context_k=args.generation_context_k,
        allowed_document_ids=corpus_document_ids,
    )
    retrieval_hash = _hash_json({
        "backend": "mock-local" if args.mock else "pgvector",
        "retrieval_limit": args.retrieval_limit,
        "generation_context_k": args.generation_context_k,
        "chunking_config_hash": mapping.chunking_config_hash,
    })
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    runner = EvaluationRunner(
        engine_builder=factory.build,
        gold_map=mapping,
        corpus_document_ids=corpus_document_ids,
        metric_cutoffs=args.metric_cutoffs,
        retrieval_config_hash=retrieval_hash,
        run_mode=run_mode,
        persistent_conversation=persistent,
        judge=judge,
        run_id=run_id,
    )
    run = runner.run_dataset(
        selected,
        prompt_variant=prompt,
        split=args.split,
        repeat_count=args.repeat,
        warmup_count=warmup_count,
    )
    git_dirty, git_diff_sha256 = _git_worktree_provenance()
    run = run.model_copy(update={
        "git_commit_sha": _git_sha(),
        "git_worktree_dirty": git_dirty,
        "git_diff_sha256": git_diff_sha256,
        "response_envelope_sha256": envelope.sha256,
        "embedding_model": "deterministic-test" if args.mock else os.getenv("EMBEDDING_MODEL"),
        "retrieval_backend": "local_json_embedding" if args.mock else "pgvector",
        "retrieval_limit": args.retrieval_limit,
        "generation_context_k": args.generation_context_k,
        "temperature": args.temperature,
        "max_output_tokens": args.max_output_tokens,
        "seed": args.seed,
        "judge_model": os.getenv("JUDGE_LLM_MODEL") if judge is not None else None,
        "conversation_mode": "persistent" if persistent else "dependency_unavailable",
    })
    config_payload = {
        "git_commit_sha": run.git_commit_sha,
        "git_worktree_dirty": run.git_worktree_dirty,
        "git_diff_sha256": run.git_diff_sha256,
        "dataset_version": run.dataset_version,
        "corpus_hash": run.corpus_hash,
        "chunking_config_hash": run.chunking_config_hash,
        "response_envelope_sha256": envelope.sha256,
        "embedding_model": run.embedding_model,
        "retrieval_backend": run.retrieval_backend,
        "retrieval_limit": args.retrieval_limit,
        "generation_context_k": args.generation_context_k,
        "llm_model": run.model,
        "temperature": args.temperature,
        "max_output_tokens": args.max_output_tokens,
        "seed": args.seed,
        "repeat_count": args.repeat,
        "split": args.split,
        "judge_model": os.getenv("JUDGE_LLM_MODEL") if judge is not None else None,
        "judge_prompt_sha256": (
            canonical_text_sha256(DEFAULT_JUDGE_PROMPT) if judge is not None else None
        ),
        "conversation_mode": "persistent" if persistent else "dependency_unavailable",
        "long_term_memory_config": "isolated_evaluation_identity",
        "retrieval_config_hash": retrieval_hash,
    }
    report = write_evaluation_report(
        run,
        cases_by_id={case.case_id: case for case in selected},
        output_root=args.output_dir,
        config=config_payload,
    )
    print(report.resolve())
    return 0


def _mock_smoke_selection(cases: list[LearningQaEvaluationCase], limit: int) -> tuple[list[LearningQaEvaluationCase], dict[str, str]]:
    singles = [case for case in cases if case.category == "single_source"][:2]
    multi = next(case for case in cases if case.category == "multi_evidence")
    unanswerable = next(case for case in cases if case.category == "unanswerable")
    anomaly = next(case for case in cases if case.category == "paraphrase")
    selected = [*singles, multi, unanswerable, anomaly][:limit]
    scenarios = {case.case_id: "valid" for case in selected}
    scenarios[unanswerable.case_id] = "abstain"
    scenarios[anomaly.case_id] = "invalid_json"
    return selected, scenarios


def build_optional_judge() -> EvaluationJudge | None:
    """Build a judge from its dedicated provider settings, never tutor settings."""
    config = JudgeConfig.from_environment()
    if config is None:
        return None
    gateway = LLMGatewayClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        max_retries=0,
    )
    prompt = DEFAULT_JUDGE_PROMPT.read_text(encoding="utf-8")
    return EvaluationJudge(gateway, config, prompt=prompt)


def validate_formal_embedding_backend() -> None:
    backend = (os.getenv("EMBEDDING_BACKEND") or "openai").strip().lower()
    if backend != "openai":
        raise EvaluationSafetyError("formal evaluation requires a remote embedding backend")


def _print_estimate(estimate) -> None:
    print(f"Dataset cases: {estimate.dataset_cases}")
    print(f"Repeats: {estimate.repeats}")
    print(f"Warmup calls: {estimate.warmup_calls}")
    print(f"Tutor LLM calls: {estimate.tutor_llm_calls}")
    print(f"Judge calls: {estimate.judge_calls}")
    print(f"Embedding calls: {estimate.embedding_calls}")
    print(f"Max tutor provider attempts (with retries): {estimate.max_tutor_provider_attempts}")
    print(f"Max Judge provider attempts (with retries): {estimate.max_judge_provider_attempts}")
    print(f"Estimated input tokens: {estimate.estimated_input_tokens}")
    print(f"Estimated output tokens: {estimate.estimated_output_tokens}")
    print(f"Estimated cost: {estimate.estimated_cost if estimate.estimated_cost is not None else 'pricing not configured'}")
    print("Remote execution enabled: false")


def compare_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    comparison = compare_summaries(
        json.loads(args.baseline.read_text(encoding="utf-8")),
        json.loads(args.candidate.read_text(encoding="utf-8")),
    )
    write_comparison(comparison, args.output)
    print(args.output.resolve())
    return 0


def estimate_main(argv: list[str] | None = None) -> int:
    parser = _run_parser()
    args = parser.parse_args(argv)
    args.dry_run = True
    args.estimate_cost = True
    return _run_evaluation(args)


def seed_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = EvaluationConfig.from_environment(allow_remote=args.allow_remote)
        database_url = config.require_database_url(require_postgres=True)
        validate_formal_embedding_backend()
        config.require_remote("embedding")
        engine = create_engine(database_url, pool_pre_ping=True)
        if not inspect(engine).has_table("documents"):
            raise EvaluationSafetyError("evaluation database is not migrated; documents table is missing")
        with Session(engine) as session:
            result = seed_evaluation_corpus(
                session,
                corpus_dir=args.corpus,
                embedding_client=build_embedding_client(),
                reset=args.reset,
                namespace=config.corpus_namespace,
            )
        mapping = build_gold_chunk_map(args.dataset, corpus_dir=args.corpus)
        output = PROJECT_ROOT / "evals" / "generated" / "learning_qa_v1_chunk_map.json"
        output.write_text(gold_chunk_map_json(mapping), encoding="utf-8", newline="\n")
        print(f"Seeded {result.document_count} documents and {result.chunk_count} chunks")
        print(output.resolve())
        return 0
    except (EvaluationSafetyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def seed_v2_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = EvaluationConfig.from_environment(allow_remote=args.allow_remote)
        database_url = config.require_database_url(require_postgres=True)
        validate_formal_embedding_backend()
        config.require_remote("embedding")
        engine = create_engine(database_url, pool_pre_ping=True)
        if not inspect(engine).has_table("documents"):
            raise EvaluationSafetyError(
                "evaluation database is not migrated; documents table is missing"
            )
        with Session(engine) as session:
            result = seed_evaluation_corpus_v2(
                session,
                corpus_dir=args.corpus,
                embedding_client=build_embedding_client(),
                reset=args.reset,
                namespace=f"{config.corpus_namespace}-chunking-v2",
            )
        mapping = build_gold_chunk_map_v2(args.dataset, corpus_dir=args.corpus)
        output = (
            PROJECT_ROOT
            / "evals"
            / "generated"
            / "learning_qa_v1_chunk_map_v2.json"
        )
        output.write_text(
            gold_chunk_map_v2_json(mapping),
            encoding="utf-8",
            newline="\n",
        )
        print(f"Seeded {result.document_count} documents and {result.chunk_count} chunks")
        print(output.resolve())
        return 0
    except (EvaluationSafetyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _git_sha() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or None


def _git_worktree_provenance() -> tuple[bool, str]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    ).stdout
    digest = hashlib.sha256()
    digest.update(subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    ).stdout)
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    ).stdout.split(b"\0")
    for raw_path in sorted(path for path in untracked if path):
        digest.update(raw_path)
        path = PROJECT_ROOT / os.fsdecode(raw_path)
        if path.is_file():
            digest.update(path.read_bytes())
    return bool(status), digest.hexdigest()
