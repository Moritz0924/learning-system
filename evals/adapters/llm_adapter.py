"""Engine-compatible strict remote LLM adapter."""
from __future__ import annotations

from typing import Any

from adaptive_tutor.phase2.telemetry import TimedLlmResult
from backend.app.services.llm_gateway import EvaluationProviderError
from evals.models import PromptVariant


class EvaluationLlmAdapter:
    def __init__(
        self,
        gateway: object,
        prompt_variant: PromptVariant,
        *,
        response_envelope: str,
        allow_remote: bool,
        temperature: float,
        max_output_tokens: int,
        seed: int | None,
    ) -> None:
        self.gateway = gateway
        self.prompt_variant = prompt_variant
        self.response_envelope = response_envelope
        self.allow_remote = allow_remote
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.seed = seed
        self.last_trace: TimedLlmResult | None = None

    def complete(self, **kwargs: Any) -> str:
        if not self.allow_remote:
            raise EvaluationProviderError(
                "remote evaluation requires --allow-remote",
                error_code="remote_not_authorized",
                request_latency_ms=0.0,
                total_latency_ms=0.0,
                retry_count=0,
            )
        self.last_trace = self.gateway.complete_timed(
            **kwargs,
            instruction_prompt=self.prompt_variant.content,
            response_envelope=self.response_envelope,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            seed=self.seed,
            strict_remote=True,
        )
        if self.last_trace.mode != "remote":
            raise EvaluationProviderError(
                "formal evaluation received a degraded completion",
                error_code="degraded_mode_forbidden",
                request_latency_ms=self.last_trace.request_latency_ms,
                total_latency_ms=self.last_trace.total_latency_ms,
                retry_count=self.last_trace.retry_count,
            )
        return self.last_trace.text
