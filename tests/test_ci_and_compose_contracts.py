from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    path = ROOT / relative_path
    assert path.is_file(), f"missing required infrastructure file: {relative_path}"
    return path.read_text(encoding="utf-8")


def test_ci_workflow_defines_all_stable_baseline_jobs() -> None:
    source = _read(".github/workflows/ci.yml")
    workflow = yaml.safe_load(source)
    jobs = workflow["jobs"]

    assert {
        "backend-tests",
        "frontend-quality",
        "frontend-e2e",
        "migration-postgres",
        "docker-build",
    } <= set(jobs)
    assert "python-version: \"3.11\"" in source
    assert "node-version: \"20\"" in source
    assert '      - "codex/**"' not in source
    assert "cancel-in-progress: true" in source
    assert "pgvector/pgvector:pg16" in source
    assert "alembic -c backend/alembic.ini downgrade 20260626_0004" in source
    assert "npm audit --omit=dev --audit-level=high" in source
    assert "npx playwright install --with-deps chromium" in source


def test_dependabot_checks_all_dependency_ecosystems_weekly() -> None:
    config = yaml.safe_load(_read(".github/dependabot.yml"))
    updates = config["updates"]
    configured = {
        (item["package-ecosystem"], item["directory"]): item["schedule"]["interval"]
        for item in updates
    }

    assert configured[("pip", "/")] == "weekly"
    assert configured[("npm", "/frontend")] == "weekly"
    assert configured[("github-actions", "/")] == "weekly"
    assert configured[("docker", "/")] == "weekly"


def test_compose_verifier_rebuilds_and_checks_the_exact_project() -> None:
    source = _read("scripts/verify-compose.ps1")

    assert "docker-compose.yml" in source
    assert "down" in source, "compose reset is required"
    assert "-v" in source
    assert "--no-cache" in source
    assert "up" in source and "-d" in source
    for service in (
        "postgres",
        "redis",
        "minio",
        "backend",
        "worker",
        "scheduler",
        "mcp",
        "frontend",
    ):
        assert service in source
    assert "/api/health/ready" in source
    assert "/openapi.json" in source
    assert source.count("Wait-ForHttpProbe -Url") == 4
    assert "http://127.0.0.1:8001/mcp" in source
    assert "ErrorDetails.Message" in source
    assert "alembic" in source and "heads" in source and "current" in source
    assert '"id", "-u"' in source
    assert "logs" in source
    assert "not_ready" in source


def test_powershell_test_runner_uses_and_cleans_a_unique_temp_directory() -> None:
    source = _read("scripts/test.ps1")

    assert "[guid]::NewGuid" in source
    assert ".Substring(0, 8)" in source, "Windows temp paths must remain short"
    assert '"--basetemp=$RunTempDir"' in source
    assert "try {" in source
    assert "finally {" in source
    assert "Remove-Item -LiteralPath" in source
    assert "StartsWith" in source


def test_e2e_runner_uses_a_run_scoped_temp_directory() -> None:
    source = _read("scripts/e2e-run.mjs")

    assert "process.pid" in source
    assert "Date.now()" in source
    assert "rmSync(tmpDir" in source
    assert "E2E artifacts preserved at" in source
