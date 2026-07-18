from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_npm_e2e_script_uses_cross_platform_process_runner():
    package_json = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    runner = (ROOT / "scripts" / "e2e-run.mjs").read_text(encoding="utf-8")

    assert package_json["scripts"]["test:e2e"] == "node ../scripts/e2e-run.mjs"
    assert "assertPortAvailable" in runner
    assert "child.exitCode" in runner
    assert "E2E_PYTHON" in runner


def test_e2e_runner_cleans_up_on_signals_without_blocking_on_playwright():
    runner = (ROOT / "scripts" / "e2e-run.mjs").read_text(encoding="utf-8")

    assert 'process.once("SIGINT"' in runner
    assert 'process.once("SIGTERM"' in runner
    assert 'process.once("SIGHUP"' in runner
    assert "await stopProcessTree" in runner
    assert "processGroupExists" in runner
    assert "playwrightProcess = startInheritedProcess" in runner
    assert 'spawnSync(\n    process.execPath,\n    [path.join(frontend, "node_modules", "@playwright"' not in runner


def test_e2e_runner_restores_next_generated_type_reference():
    runner = (ROOT / "scripts" / "e2e-run.mjs").read_text(encoding="utf-8")

    assert "nextEnvOriginal" in runner
    assert "restoreNextEnv" in runner
    assert "writeFileSync(nextEnvPath" in runner


def test_playwright_config_uses_runner_supplied_base_url_without_nested_lifecycle():
    config = (ROOT / "frontend" / "playwright.config.ts").read_text(encoding="utf-8")

    assert "PLAYWRIGHT_BASE_URL" in config
    assert "webServer:" not in config


def test_backend_test_script_propagates_pytest_failure_exit_code():
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell is not None, "PowerShell is required to verify scripts/test.ps1"
    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "test.ps1"),
            "-SkipCompile",
            "tests/__missing_pytest_target__.py",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )

    assert completed.returncode != 0
    assert "not found" in completed.stdout.lower() or "no tests ran" in completed.stdout.lower()


def test_compose_runs_periodic_document_outbox_dispatcher():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "scheduler:" in compose
    assert '["celery", "-A", "backend.app.worker", "beat", "--loglevel=info"]' in compose


def test_compose_allows_root_env_and_shell_credentials_to_override_examples():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    for service_name in ("backend", "worker", "scheduler"):
        service = compose["services"][service_name]
        assert {"path": ".env", "required": False} in service["env_file"]
        assert service["environment"]["LLM_API_KEY"] == "${LLM_API_KEY:-}"
        assert service["environment"]["DATABASE_URL"].startswith("${DATABASE_URL:-")
        assert service["environment"]["JWT_SECRET_KEY"] == "${JWT_SECRET_KEY:-}"
        assert service["environment"]["AUTH_COOKIE_SECURE"] == "${AUTH_COOKIE_SECURE:-false}"
    assert compose["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] == "${POSTGRES_PASSWORD:-tutor}"
    assert "$${POSTGRES_USER}" in compose["services"]["postgres"]["healthcheck"]["test"][-1]
    assert compose["services"]["minio"]["environment"]["MINIO_ROOT_USER"] == "${MINIO_ACCESS_KEY:-minioadmin}"
    assert compose["services"]["minio"]["environment"]["MINIO_ROOT_PASSWORD"] == "${MINIO_SECRET_KEY:-minioadmin}"


def test_docker_build_context_excludes_local_and_generated_artifacts():
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    patterns = {line.strip() for line in dockerignore if line.strip() and not line.startswith("#")}

    assert {
        ".git",
        ".venv",
        "**/__pycache__",
        ".pytest_cache",
        "frontend/node_modules",
        "frontend/.next",
        "frontend/playwright-report",
        "frontend/test-results",
    } <= patterns


def test_application_images_run_as_non_root_users():
    backend_dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    frontend_dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")

    assert "\nUSER app\n" in backend_dockerfile
    assert "\nUSER node\n" in frontend_dockerfile


def test_backend_image_tolerates_slow_package_downloads():
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.count("apt-get -o Acquire::Retries=10 -o Acquire::http::Timeout=120") == 2
    assert "pip install --timeout 120 --retries 10 --no-cache-dir ." in dockerfile


def test_git_ignores_next_build_outputs():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    patterns = {line.strip() for line in gitignore if line.strip() and not line.startswith("#")}

    assert "frontend/.next/" in patterns


def test_compose_builds_the_shared_backend_image_once():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["backend"]["image"] == "learning-system-backend:local"
    assert services["worker"]["image"] == services["backend"]["image"]
    assert services["scheduler"]["image"] == services["backend"]["image"]
    assert "build" in services["backend"]
    assert "build" not in services["worker"]
    assert "build" not in services["scheduler"]


def test_compose_contract_yaml_parser_is_a_declared_dev_dependency():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()

    assert '"pyyaml>=6.0"' in pyproject


def test_frontend_public_api_url_is_build_time_only_and_documented():
    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    frontend_service = compose["services"]["frontend"]
    build_args = frontend_service["build"]["args"]
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    build_position = dockerfile.index("RUN npm run build")
    assert dockerfile.index("ARG NEXT_PUBLIC_API_BASE_URL") < build_position
    assert dockerfile.index("ENV NEXT_PUBLIC_API_BASE_URL") < build_position
    assert "NEXT_PUBLIC_API_BASE_URL" in build_args
    assert "NEXT_PUBLIC_API_BASE_URL" not in frontend_service.get("environment", {})
    assert "NEXT_PUBLIC_API_BASE_URL=" not in env_example
    assert "NEXT_PUBLIC_API_BASE_URL" in readme
    assert "build-time" in readme.lower()
