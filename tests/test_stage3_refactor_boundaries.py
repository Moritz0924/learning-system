from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _imports_from_stage3(path: str) -> list[str]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "backend.app.services.stage3":
            imports.append(node.module)
    return imports


def test_stage3_implementation_is_split_into_target_modules():
    expected_modules = [
        "backend/app/application/assessment_service.py",
        "backend/app/application/document_service.py",
        "backend/app/application/learning_activity_service.py",
        "backend/app/application/planning_service.py",
        "backend/app/application/tutor_service.py",
        "backend/app/core/exceptions.py",
        "backend/app/infrastructure/persistence/repositories/assessment_repository.py",
        "backend/app/infrastructure/persistence/repositories/audit_repository.py",
        "backend/app/infrastructure/persistence/repositories/plan_repository.py",
        "backend/app/infrastructure/persistence/repositories/rag_repository.py",
        "backend/app/infrastructure/persistence/repositories/state_repository.py",
    ]

    missing = [path for path in expected_modules if not (ROOT / path).exists()]

    assert missing == []


def test_runtime_code_no_longer_imports_stage3_facade():
    runtime_files = [
        "backend/app/routers/assessments.py",
        "backend/app/routers/documents.py",
        "backend/app/routers/plans.py",
        "backend/app/routers/tasks.py",
        "backend/app/routers/tutor.py",
        "backend/app/services/learning.py",
        "backend/app/worker.py",
    ]

    offenders = [path for path in runtime_files if _imports_from_stage3(path)]

    assert offenders == []


def test_stage3_module_is_only_a_compatibility_facade():
    source = (ROOT / "backend/app/services/stage3.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    definitions = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    assert definitions == []
    assert "Phase2TutorEngine" not in source
    assert "session.commit()" not in source
