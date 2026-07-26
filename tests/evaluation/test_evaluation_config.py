from __future__ import annotations

import os

import pytest


def test_evaluation_database_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    from evals.runner.evaluation_config import EvaluationConfig, EvaluationSafetyError

    monkeypatch.delenv("EVALUATION_DATABASE_URL", raising=False)
    with pytest.raises(EvaluationSafetyError, match="EVALUATION_DATABASE_URL"):
        EvaluationConfig.from_environment().require_database_url()


def test_shared_database_is_rejected_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from evals.runner.evaluation_config import EvaluationConfig, EvaluationSafetyError

    url = "postgresql+psycopg://eval:secret@localhost/learning"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("EVALUATION_DATABASE_URL", url)
    monkeypatch.delenv("EVALUATION_ALLOW_SHARED_DATABASE", raising=False)

    with pytest.raises(EvaluationSafetyError, match="equals DATABASE_URL"):
        EvaluationConfig.from_environment().require_database_url()


def test_shared_database_detection_ignores_credentials_and_driver_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    from evals.runner.evaluation_config import EvaluationConfig, EvaluationSafetyError

    monkeypatch.setenv("DATABASE_URL", "postgresql://app:one@db.example:5432/learning")
    monkeypatch.setenv("EVALUATION_DATABASE_URL", "postgresql+psycopg://eval:two@db.example:5432/learning")
    monkeypatch.delenv("EVALUATION_ALLOW_SHARED_DATABASE", raising=False)

    with pytest.raises(EvaluationSafetyError, match="equals DATABASE_URL"):
        EvaluationConfig.from_environment().require_database_url()


def test_remote_provider_requires_explicit_allow_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from evals.runner.evaluation_config import EvaluationConfig, EvaluationSafetyError

    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    config = EvaluationConfig.from_environment(allow_remote=False)

    with pytest.raises(EvaluationSafetyError, match="--allow-remote"):
        config.require_remote("llm")


def test_current_engine_does_not_claim_persistent_conversation_support() -> None:
    from evals.runner.evaluation_config import persistent_conversation_available

    assert persistent_conversation_available() is False


def test_environment_defaults_use_evaluation_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    from evals.runner.evaluation_config import EvaluationConfig

    monkeypatch.delenv("EVALUATION_CORPUS_NAMESPACE", raising=False)
    config = EvaluationConfig.from_environment()
    assert config.corpus_namespace == "learning-qa-v1"
    assert os.getenv("EVALUATION_CORPUS_NAMESPACE") is None
