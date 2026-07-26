from __future__ import annotations

import json

from adaptive_tutor.phase2.telemetry import TimedLlmResult


def test_judge_is_disabled_unless_all_independent_settings_exist(monkeypatch) -> None:
    from evals.runner.judge import JudgeConfig

    monkeypatch.setenv("JUDGE_LLM_BASE_URL", "https://judge.example/v1")
    monkeypatch.delenv("JUDGE_LLM_API_KEY", raising=False)
    monkeypatch.setenv("JUDGE_LLM_MODEL", "judge-model")

    assert JudgeConfig.from_environment() is None


def test_judge_returns_strict_verdict_and_does_not_crash_on_bad_json() -> None:
    from evals.runner.judge import EvaluationJudge, JudgeConfig

    class Gateway:
        output = json.dumps({
            "citation_supported": True,
            "citation_support_by_index": [True],
            "contains_unsupported_claim": False,
            "correctly_abstained": None,
            "missing_answer_points": [],
            "reason": "supported",
        })
        last_payload = None

        def complete_timed(self, **kwargs):
            self.last_payload = json.loads(kwargs["prompt"])
            return TimedLlmResult(
                text=self.output,
                model="judge-model",
                mode="remote",
                request_latency_ms=1,
                parse_latency_ms=0,
                total_latency_ms=1,
                retry_count=0,
            )

    gateway = Gateway()
    judge = EvaluationJudge(
        gateway,
        JudgeConfig("https://judge.example/v1", "secret", "judge-model"),
        prompt="judge prompt",
    )
    good = judge.grade(
        question="q",
        answer="a",
        citations=[{"chunk_id": "c1", "document_id": "d1"}],
        evidence=[{"chunk_id": "c1", "document_id": "d1", "content": "support"}],
        gold_evidence=[{"evidence_id": "e1", "text": "support"}],
        gold_answer_points=["point"],
    )
    assert good.verdict is not None and good.verdict.citation_supported is True
    assert good.error_code is None
    assert gateway.last_payload["cited_evidence"][0]["content"] == "support"
    assert gateway.last_payload["gold_answer_points"] == ["point"]

    gateway.output = "not-json"
    bad = judge.grade(question="q", answer="a", citations=[], evidence=[])
    assert bad.verdict is None
    assert bad.error_code == "judge_response_invalid"
    assert bad.attempt_count == 1

    gateway.output = json.dumps({
        "citation_supported": True,
        "citation_support_by_index": [True],
        "contains_unsupported_claim": False,
        "correctly_abstained": None,
        "missing_answer_points": [],
        "reason": "wrong length",
    })
    mismatch = judge.grade(question="q", answer="a", citations=[], evidence=[])
    assert mismatch.verdict is None
    assert mismatch.error_code == "judge_response_invalid"


def test_human_override_preserves_original_judge_result() -> None:
    from evals.models import JudgeVerdict
    from evals.runner.judge import apply_human_override
    from tests.evaluation.test_reporting import _run

    original = JudgeVerdict(citation_supported=False, contains_unsupported_claim=True, reason="judge")
    correction = JudgeVerdict(citation_supported=True, contains_unsupported_claim=False, reason="human")
    result = _run().results[0].model_copy(update={"grader_mode": "llm_judge", "judge_result": original})

    overridden = apply_human_override(
        result,
        verdict=correction,
        reason="manual evidence review",
        reviewer="reviewer-1",
    )

    assert overridden.grader_mode == "human_override"
    assert overridden.judge_result == original
    assert overridden.human_override_result == correction
    assert overridden.human_override_reason == "manual evidence review"
    assert overridden.answer.citation_support_rate == 1.0
    assert overridden.answer.contains_unsupported_claim is False
