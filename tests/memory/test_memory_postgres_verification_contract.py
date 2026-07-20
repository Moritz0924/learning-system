from pathlib import Path


def test_postgres_ci_runs_real_memory_repository_concurrency_verification() -> None:
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "verify-postgres-memory-repository.py"
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert script.exists()
    source = script.read_text(encoding="utf-8")
    assert "SQLAlchemyMemoryRepository" in source
    assert "ThreadPoolExecutor" in source
    assert "with_for_update" in source or "for_update=True" in source
    assert "outer rollback" in source.lower()
    assert "verify-postgres-memory-repository.py" in workflow
