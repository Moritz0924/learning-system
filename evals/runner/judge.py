"""Optional independently configured semantic judge."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from backend.app.services.llm_gateway import EvaluationProviderError
from evals.models import EvaluationCaseResult, JudgeVerdict


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


@dataclass(frozen=True)
class JudgeConfig:
    base_url: str
    api_key: str
    model: str

    @classmethod
    def from_environment(cls) -> "JudgeConfig | None":
        values = (_env("JUDGE_LLM_BASE_URL"), _env("JUDGE_LLM_API_KEY"), _env("JUDGE_LLM_MODEL"))
        if not all(values):
            return None
        return cls(base_url=values[0], api_key=values[1], model=values[2])  # type: ignore[arg-type]


@dataclass(frozen=True)
class JudgeOutcome:
    verdict: JudgeVerdict | None
    error_code: str | None
    reason: str | None
    attempt_count: int


class EvaluationJudge:
    def __init__(self, gateway: object, config: JudgeConfig, *, prompt: str) -> None:
        self.gateway = gateway
        self.config = config
        self.prompt = prompt

    def grade(
        self,
        *,
        question: str,
        answer: str,
        citations: list[dict[str, str]],
        evidence: list[dict[str, Any]],
        gold_evidence: list[dict[str, Any]] | None = None,
        gold_answer_points: list[str] | None = None,
    ) -> JudgeOutcome:
        payload = json.dumps(
            {
                "question": question,
                "answer": answer,
                "citations": citations,
                "cited_evidence": evidence,
                "gold_evidence": gold_evidence or [],
                "gold_answer_points": gold_answer_points or [],
            },
            ensure_ascii=False,
        )
        try:
            result = self.gateway.complete_timed(
                role="judge",
                prompt=payload,
                instruction_prompt=self.prompt,
                response_envelope=None,
                temperature=0,
                max_output_tokens=512,
                seed=0,
                strict_remote=True,
            )
            verdict = JudgeVerdict.model_validate_json(result.text)
            expected_citations = len(citations)
            if len(verdict.citation_support_by_index) != expected_citations:
                raise ValueError(
                    "citation_support_by_index length must match the citations array"
                )
            if expected_citations and verdict.citation_supported is not None:
                if verdict.citation_supported != all(verdict.citation_support_by_index):
                    raise ValueError(
                        "citation_supported must equal all(citation_support_by_index)"
                    )
            return JudgeOutcome(verdict=verdict, error_code=None, reason=verdict.reason, attempt_count=1)
        except EvaluationProviderError as exc:
            return JudgeOutcome(verdict=None, error_code="judge_provider_error", reason=str(exc), attempt_count=1)
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return JudgeOutcome(verdict=None, error_code="judge_response_invalid", reason=str(exc), attempt_count=1)


def apply_human_override(
    result: EvaluationCaseResult,
    *,
    verdict: JudgeVerdict,
    reason: str,
    reviewer: str,
) -> EvaluationCaseResult:
    """Apply a traceable human correction without overwriting the original judge verdict."""
    if not reason.strip() or not reviewer.strip():
        raise ValueError("human override requires a reason and reviewer")
    answer = result.answer.model_copy(update={
        "citation_support_rate": (
            sum(verdict.citation_support_by_index) / len(verdict.citation_support_by_index)
            if verdict.citation_support_by_index
            and len(verdict.citation_support_by_index) == result.answer.citation_count
            else float(verdict.citation_supported)
            if verdict.citation_supported is not None and result.answer.citation_count == 1
            else None
        ),
        "citation_semantically_graded_count": (
            result.answer.citation_count
            if verdict.citation_support_by_index
            and len(verdict.citation_support_by_index) == result.answer.citation_count
            else 1
            if verdict.citation_supported is not None and result.answer.citation_count == 1
            else 0
        ),
        "contains_unsupported_claim": verdict.contains_unsupported_claim,
        "correctly_abstained": verdict.correctly_abstained,
    })
    return result.model_copy(update={
        "grader_mode": "human_override",
        "answer": answer,
        "human_override_result": verdict,
        "human_override_reason": reason.strip(),
        "human_reviewer": reviewer.strip(),
    })
