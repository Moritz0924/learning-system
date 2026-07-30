from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)
DATASET = ROOT / "evals" / "datasets" / "learning_qa_v1.jsonl"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env["NO_PROXY"] = "*"
    if env:
        process_env.update(env)
    return subprocess.run(
        [str(PYTHON), *args],
        cwd=ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_verify_and_build_chunk_map_commands(tmp_path: Path) -> None:
    verify = _run("scripts/verify-evaluation-dataset.py", "--dataset", str(DATASET))
    assert verify.returncode == 0, verify.stderr
    assert "40" in verify.stdout

    output = tmp_path / "map.json"
    mapping = _run(
        "scripts/build-evaluation-chunk-map.py",
        "--dataset", str(DATASET),
        "--output", str(output),
    )
    assert mapping.returncode == 0, mapping.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload["cases"]) == 35

    output_v2 = tmp_path / "map-v2.json"
    mapping_v2 = _run(
        "scripts/build-evaluation-chunk-map-v2.py",
        "--dataset", str(DATASET),
        "--output", str(output_v2),
    )
    assert mapping_v2.returncode == 0, mapping_v2.stderr
    payload_v2 = json.loads(output_v2.read_text(encoding="utf-8"))
    assert len(payload_v2["cases"]) == 35
    assert payload_v2["chunking_config_hash"] != payload["chunking_config_hash"]


def test_dry_run_reports_exact_calls_without_database_or_remote_access() -> None:
    result = _run(
        "scripts/run-rag-evaluation.py",
        "--dataset", str(DATASET),
        "--split", "test",
        "--repeat", "3",
        "--metric-cutoffs", "1", "3", "5",
        "--retrieval-limit", "5",
        "--generation-context-k", "5",
        "--dry-run",
        "--estimate-cost",
    )
    assert result.returncode == 0, result.stderr
    assert "Tutor LLM calls: 48" in result.stdout
    assert "Embedding calls: 48" in result.stdout
    assert "Remote execution enabled: false" in result.stdout


def test_remote_run_without_allow_remote_is_rejected_before_database_access() -> None:
    result = _run(
        "scripts/run-rag-evaluation.py",
        "--dataset", str(DATASET),
        "--split", "test",
        "--repeat", "1",
        env={"EVALUATION_DATABASE_URL": "postgresql+psycopg://eval:secret@localhost/eval"},
    )
    assert result.returncode != 0
    assert "--allow-remote" in result.stderr


def test_mock_smoke_runs_full_pipeline_and_writes_nonrepresentative_report(tmp_path: Path) -> None:
    result = _run(
        "scripts/run-rag-evaluation.py",
        "--dataset", str(DATASET),
        "--split", "development",
        "--max-cases", "5",
        "--mock",
        "--metric-cutoffs", "1", "3", "5",
        "--retrieval-limit", "5",
        "--generation-context-k", "5",
        "--repeat", "1",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    report_path = Path(result.stdout.strip().splitlines()[-1])
    summary = json.loads((report_path / "summary.json").read_text(encoding="utf-8"))
    run = json.loads((report_path / "run.json").read_text(encoding="utf-8"))
    assert run["run_mode"] == "mock_smoke"
    assert run["quality_metrics_are_representative"] is False
    assert sum(summary["execution_status"].values()) == 5
    assert summary["answer_quality"]["unsupported_answer_rate"] is None


def test_independent_judge_uses_only_judge_provider_settings(monkeypatch) -> None:
    from evals.runner.cli import build_optional_judge

    monkeypatch.setenv("LLM_BASE_URL", "https://tutor.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "tutor-secret")
    monkeypatch.setenv("LLM_MODEL", "tutor-model")
    monkeypatch.setenv("JUDGE_LLM_BASE_URL", "https://judge.example/v1")
    monkeypatch.setenv("JUDGE_LLM_API_KEY", "judge-secret")
    monkeypatch.setenv("JUDGE_LLM_MODEL", "judge-model")

    judge = build_optional_judge()

    assert judge is not None
    assert judge.gateway.base_url == "https://judge.example/v1"
    assert judge.gateway.api_key == "judge-secret"
    assert judge.gateway.model == "judge-model"


def test_formal_evaluation_rejects_deterministic_embedding_backend(monkeypatch) -> None:
    import pytest

    from evals.runner.cli import validate_formal_embedding_backend
    from evals.runner.evaluation_config import EvaluationSafetyError

    monkeypatch.setenv("EMBEDDING_BACKEND", "deterministic")
    with pytest.raises(EvaluationSafetyError, match="remote embedding"):
        validate_formal_embedding_backend()
