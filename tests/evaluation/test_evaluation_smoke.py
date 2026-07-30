from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)
DATASET = ROOT / "evals" / "datasets" / "learning_qa_v1.jsonl"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["NO_PROXY"] = "*"
    for key in (
        "EVALUATION_DATABASE_URL",
        "LLM_API_KEY",
        "EMBEDDING_API_KEY",
        "JUDGE_LLM_API_KEY",
    ):
        environment.pop(key, None)
    return subprocess.run(
        [str(PYTHON), *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _mock_run(prompt: Path, output_root: Path) -> Path:
    completed = _run(
        "scripts/run-rag-evaluation.py",
        "--dataset", str(DATASET),
        "--index-schema", "legacy-v1",
        "--prompt", str(prompt),
        "--split", "development",
        "--max-cases", "5",
        "--mock",
        "--metric-cutoffs", "1", "3", "5",
        "--retrieval-limit", "5",
        "--generation-context-k", "5",
        "--repeat", "1",
        "--output-dir", str(output_root),
    )
    assert completed.returncode == 0, completed.stderr
    return Path(completed.stdout.strip().splitlines()[-1])


def test_two_mock_smoke_runs_generate_full_reports_and_comparison(tmp_path: Path) -> None:
    baseline = _mock_run(ROOT / "evals" / "prompts" / "tutor_baseline_v1.txt", tmp_path / "baseline")
    candidate = _mock_run(ROOT / "evals" / "prompts" / "tutor_candidate_v2.txt", tmp_path / "candidate")
    comparison = tmp_path / "comparison" / "comparison.md"

    completed = _run(
        "scripts/compare-prompt-evaluations.py",
        "--baseline", str(baseline / "summary.json"),
        "--candidate", str(candidate / "summary.json"),
        "--output", str(comparison),
    )

    assert completed.returncode == 0, completed.stderr
    expected = {
        "run.json", "config.json", "cases.jsonl", "retrieval-results.csv",
        "answer-results.csv", "summary.json", "summary.md", "failed-cases.jsonl",
        "human-review.csv",
    }
    assert {path.name for path in baseline.iterdir()} == expected
    assert {path.name for path in candidate.iterdir()} == expected
    assert comparison.exists() and comparison.with_suffix(".json").exists()
    run = json.loads((candidate / "run.json").read_text(encoding="utf-8"))
    summary = json.loads((candidate / "summary.json").read_text(encoding="utf-8"))
    assert run["run_mode"] == "mock_smoke"
    assert run["quality_metrics_are_representative"] is False
    assert sum(summary["execution_status"].values()) == 5
    assert summary["answer_quality"]["citation_support_rate"] is None
    assert summary["answer_quality"]["unsupported_answer_rate"] is None
