"""Minimal read-only tool router with deterministic safety guards."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Mapping

from .t3_contracts import Thread3ErrorCode, ToolPolicy


class ToolRouterError(RuntimeError):
    def __init__(self, code: Thread3ErrorCode, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolResult:
    value: Any
    cache_hit: bool
    truncated: bool
    untrusted: bool
    fingerprint: str


class ToolRouter:
    def __init__(self, registry: Mapping[str, Callable[[dict[str, Any]], Any]], *, policy: ToolPolicy | None = None):
        self.registry = dict(registry)
        self.policy = policy or ToolPolicy()
        self._cache: dict[tuple[str, str, str], ToolResult] = {}
        self._calls: dict[str, int] = {}

    def execute(
        self,
        *,
        run_id: str,
        user_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        if tool_name not in self.registry:
            raise ToolRouterError(Thread3ErrorCode.TOOL_NOT_ALLOWED, "tool is not allowed")
        argument_json = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(argument_json.encode("utf-8")) > self.policy.max_argument_bytes:
            raise ToolRouterError(Thread3ErrorCode.TOOL_ARGUMENT_INVALID, "tool arguments are too large")
        fingerprint = sha256(f"{tool_name}\n{argument_json}".encode("utf-8")).hexdigest()
        cache_key = (user_id, run_id, fingerprint)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return ToolResult(cached.value, True, cached.truncated, True, fingerprint)
        if self._calls.get(run_id, 0) >= self.policy.max_calls_per_run:
            raise ToolRouterError(Thread3ErrorCode.TOOL_BUDGET_EXCEEDED, "tool call budget exceeded")
        self._calls[run_id] = self._calls.get(run_id, 0) + 1
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.registry[tool_name], arguments)
        try:
            raw = future.result(timeout=self.policy.timeout_seconds)
        except FutureTimeout as exc:
            future.cancel()
            raise ToolRouterError(Thread3ErrorCode.TOOL_TIMEOUT, "tool timed out") from exc
        except Exception as exc:
            raise ToolRouterError(Thread3ErrorCode.TOOL_EXECUTION_FAILED, "tool execution failed") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        raw_json = json.dumps(raw, ensure_ascii=False, default=str)
        if len(raw_json.encode("utf-8")) > self.policy.max_raw_result_bytes:
            raise ToolRouterError(Thread3ErrorCode.TOOL_RESULT_TOO_LARGE, "tool result is too large")
        value, truncated = self._normalize(raw)
        normalized = json.dumps(value, ensure_ascii=False, default=str)
        if len(normalized) > self.policy.max_normalized_result_chars:
            value = normalized[: self.policy.max_normalized_result_chars]
            truncated = True
        result = ToolResult(value, False, truncated, True, fingerprint)
        self._cache[cache_key] = result
        return result

    def _normalize(self, value: Any) -> tuple[Any, bool]:
        truncated = False
        if isinstance(value, list):
            truncated = len(value) > self.policy.max_result_items
            value = value[: self.policy.max_result_items]
            return [self._sanitize(item) for item in value], truncated
        if isinstance(value, dict):
            if isinstance(value.get("items"), list):
                items = value["items"]
                truncated = len(items) > self.policy.max_result_items
                value = {**value, "items": items[: self.policy.max_result_items]}
            return self._sanitize(value), truncated
        return self._sanitize(value), False

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, str):
            return re.sub(
                r"ignore previous instructions|system prompt|developer message",
                "[filtered untrusted instruction]",
                value,
                flags=re.IGNORECASE,
            )
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._sanitize(item) for key, item in value.items()}
        return value
