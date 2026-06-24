from __future__ import annotations

import os
from typing import Any

import httpx


class LLMGatewayClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or "").rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("LLM_API_KEY")
        self.model = model or os.getenv("LLM_MODEL") or "stage3-mock-model"
        self.http_client = http_client or httpx.Client(timeout=15)

    def complete(self, *, role: str, prompt: str, context: list[Any] | None = None) -> str:
        if not self.base_url or not self.api_key:
            return self._offline_complete(role=role, prompt=prompt, context=context or [])

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an adaptive AI application development tutor. "
                    "Use supplied source context and keep citations traceable."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        if context:
            messages.insert(
                1,
                {
                    "role": "system",
                    "content": "Source context:\n"
                    + "\n".join(
                        f"- {getattr(item, 'citation_label', 'source')}: {getattr(item, 'content', item)}"
                        for item in context
                    ),
                },
            )

        response = self.http_client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "messages": messages, "temperature": 0.2},
        )
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"]

    @staticmethod
    def _offline_complete(*, role: str, prompt: str, context: list[Any]) -> str:
        if context:
            label = getattr(context[0], "citation_label", "trusted source")
            return f"{prompt} 先从学习目标拆解问题，再用 {label} 的资料校准理解。"
        return f"{role}: {prompt}"
