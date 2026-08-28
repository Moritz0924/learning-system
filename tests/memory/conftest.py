import pytest


@pytest.fixture(autouse=True)
def _inject_tutor_model_for_memory_workflow_tests(monkeypatch):
    """Memory tests isolate persistence behavior from user AI configuration."""
    monkeypatch.setattr(
        "backend.app.application.config_service.RuntimeResolver.resolve_tutor_text",
        lambda resolver, **_kwargs: resolver.llm_factory(),
    )
