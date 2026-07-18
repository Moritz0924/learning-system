from __future__ import annotations

import ast
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.replace("\\", "/") for line in result.stdout.splitlines() if line}


def test_generated_artifacts_are_not_tracked() -> None:
    generated_parts = {
        ".next",
        "playwright-report",
        "test-results",
        ".tmp",
        ".pytest_cache",
        "__pycache__",
    }

    offenders = sorted(
        path
        for path in _tracked_files()
        if any(part in generated_parts for part in Path(path).parts)
    )

    assert offenders == []


def test_runtime_routers_do_not_accept_legacy_identity_headers() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "backend/app/routers").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "X-User-Id" in source or "x-user-id" in source.lower():
            offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []


def test_stage3_is_only_a_compatibility_facade() -> None:
    path = ROOT / "backend/app/services/stage3.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    definitions = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    assert definitions == []
    assert "session.commit()" not in source


def test_alembic_has_exactly_one_head() -> None:
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in sorted((ROOT / "backend/alembic/versions").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        values: dict[str, object] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                values[target.id] = ast.literal_eval(node.value)
        revisions.add(str(values["revision"]))
        down_revision = values.get("down_revision")
        if isinstance(down_revision, tuple):
            parents.update(str(value) for value in down_revision if value is not None)
        elif down_revision is not None:
            parents.add(str(down_revision))

    assert len(revisions - parents) == 1


def test_main_has_no_duplicate_raise_statement() -> None:
    source = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")

    assert "    raise exc\n    raise exc" not in source
