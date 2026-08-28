from __future__ import annotations

import asyncio
from base64 import b64decode
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Callable, Literal
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.secrets import SecretStore
from backend.app.models import (
    UserCapabilityBinding,
    UserModelProfile,
    UserPromptSkill,
    UserSecretReference,
)
from backend.app.services.document_parsing.models import (
    DocumentFileType,
    SourceElementType,
    VisionContext,
    VisionEnrichmentStatus,
)
from backend.app.services.embeddings import (
    EmbeddingUnavailable,
    OpenAICompatibleEmbeddingClient,
    build_embedding_client,
)
from backend.app.services.llm_gateway import EvaluationProviderError, LLMGatewayClient
from backend.app.services.provider_urls import (
    canonicalize_provider_base_url,
    has_sensitive_query_name,
)
from backend.app.services.vision_understanding import VisionClient


Capability = Literal["chat", "reasoning", "vision", "embedding"]
_CAPABILITIES = {"chat", "reasoning", "vision", "embedding"}
_ZHIPU_EMBEDDING_3_DIMENSIONS = frozenset({256, 512, 1024, 2048})
_TUTOR_REQUEST_CONTEXT_CHAR_BUDGET = 8_192
_TINY_PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+XfEXGQAAAABJRU5ErkJggg=="
)


class RuntimeResolutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SkillSelectionNotFound(LookupError):
    pass


class SkillSelectionInvalid(ValueError):
    pass


@dataclass(frozen=True)
class SkillSelection:
    skill_ids: tuple[str, ...] = ()
    instruction_prompt: str | None = None
    model_profile_id: str | None = None
    capability: str | None = None


@dataclass(frozen=True)
class ModelTestOutcome:
    status: Literal["success", "failed"]
    code: str | None = None


class StrictEmbeddingClient:
    def __init__(self, client: OpenAICompatibleEmbeddingClient) -> None:
        self._client = client

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)

    def embed(self, text: str) -> list[float]:
        try:
            return self._client.embed(text)
        except EmbeddingUnavailable:
            raise RuntimeResolutionError("runtime.provider_call_failed") from None

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            return self._client.embed_batch(texts)
        except EmbeddingUnavailable:
            raise RuntimeResolutionError("runtime.provider_call_failed") from None


class StrictVisionClient:
    def __init__(self, client: VisionClient) -> None:
        self._client = client

    @property
    def http_client(self):
        return self._client.http_client

    @http_client.setter
    def http_client(self, value) -> None:
        self._client.http_client = value

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)

    async def analyze_image(self, *args, **kwargs):
        result = await self._client.analyze_image(*args, **kwargs)
        if result.status in {
            VisionEnrichmentStatus.FAILED,
            VisionEnrichmentStatus.UNAVAILABLE,
        }:
            raise RuntimeResolutionError("runtime.provider_call_failed")
        return result

class RuntimeResolver:
    def __init__(
        self,
        session: Session,
        *,
        user_id: str,
        secret_store: SecretStore | None,
        llm_factory: Callable[..., LLMGatewayClient] = LLMGatewayClient,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.secret_store = secret_store
        self.llm_factory = llm_factory

    def resolve(
        self,
        capability: Capability,
        *,
        model_profile_id: str | None = None,
        instruction_prompt: str | None = None,
    ) -> object:
        if capability not in _CAPABILITIES:
            raise RuntimeResolutionError("runtime.capability_invalid")
        if model_profile_id is not None:
            return self.resolve_profile(
                model_profile_id,
                expected_capability=capability,
                instruction_prompt=instruction_prompt,
            )
        binding = self.session.scalar(
            select(UserCapabilityBinding).where(
                UserCapabilityBinding.user_id == self.user_id,
                UserCapabilityBinding.capability == capability,
            )
        )
        if binding is None:
            return self._environment_client(capability)
        return self.resolve_profile(
            binding.model_profile_id,
            expected_capability=capability,
            instruction_prompt=instruction_prompt,
        )

    def resolve_tutor_text(self, *, instruction_prompt: str | None = None) -> LLMGatewayClient:
        """Resolve the user-managed text model for tutor conversations only."""
        chat_binding = self.session.scalar(
            select(UserCapabilityBinding).where(
                UserCapabilityBinding.user_id == self.user_id,
                UserCapabilityBinding.capability == "chat",
            )
        )
        if chat_binding is not None:
            return self.resolve(
                "chat", instruction_prompt=instruction_prompt
            )  # type: ignore[return-value]
        reasoning_binding = self.session.scalar(
            select(UserCapabilityBinding).where(
                UserCapabilityBinding.user_id == self.user_id,
                UserCapabilityBinding.capability == "reasoning",
            )
        )
        if reasoning_binding is not None:
            return self.resolve(
                "reasoning", instruction_prompt=instruction_prompt
            )  # type: ignore[return-value]
        raise RuntimeResolutionError("runtime.tutor_model_unconfigured")

    def resolve_profile(
        self,
        model_profile_id: str,
        *,
        expected_capability: str | None = None,
        instruction_prompt: str | None = None,
    ) -> object:
        profile = self.session.scalar(
            select(UserModelProfile).where(
                UserModelProfile.id == model_profile_id,
                UserModelProfile.user_id == self.user_id,
            )
        )
        if profile is None:
            raise RuntimeResolutionError("runtime.profile_invalid")
        if not profile.enabled:
            raise RuntimeResolutionError("runtime.profile_disabled")
        if (
            not profile.name.strip()
            or not profile.model_name.strip()
            or not profile.base_url.strip()
            or profile.provider != "openai_compatible"
            or profile.capability not in _CAPABILITIES
            or (
                expected_capability is not None
                and profile.capability != expected_capability
            )
        ):
            raise RuntimeResolutionError("runtime.profile_invalid")
        try:
            base_url = canonicalize_provider_base_url(profile.base_url)
        except ValueError:
            raise RuntimeResolutionError("runtime.profile_invalid") from None
        if has_sensitive_query_name(base_url):
            raise RuntimeResolutionError("runtime.profile_invalid")
        reference = self.session.scalar(
            select(UserSecretReference).where(
                UserSecretReference.user_id == self.user_id,
                UserSecretReference.owner_type == "model",
                UserSecretReference.owner_id == profile.id,
                UserSecretReference.slot == "api_key",
                UserSecretReference.configured.is_(True),
            )
        )
        if reference is None or self.secret_store is None:
            raise RuntimeResolutionError("runtime.credential_missing")
        try:
            api_key = self.secret_store.get(reference.secret_ref)
        except Exception:
            raise RuntimeResolutionError("runtime.credential_missing") from None
        if not api_key:
            raise RuntimeResolutionError("runtime.credential_missing")
        if profile.capability in {"chat", "reasoning"}:
            return self.llm_factory(
                base_url=base_url,
                api_key=api_key,
                model=profile.model_name,
                strict_remote_default=True,
                default_instruction_prompt=instruction_prompt,
            )
        if profile.capability == "vision":
            return StrictVisionClient(
                VisionClient(
                    base_url=base_url,
                    api_key=api_key,
                    model=profile.model_name,
                    thinking_enabled=_is_deepseek_endpoint(base_url),
                )
            )
        if not _embedding_profile_dimensions_valid(profile):
            raise RuntimeResolutionError("runtime.profile_invalid")
        return StrictEmbeddingClient(
            OpenAICompatibleEmbeddingClient(
                base_url=base_url,
                api_key=api_key,
                model=profile.model_name,
                dimensions=profile.dimensions,
            )
        )

    def _environment_client(self, capability: Capability) -> object:
        if capability in {"chat", "reasoning"}:
            return self.llm_factory()
        if capability == "vision":
            return VisionClient()
        return build_embedding_client()


def resolve_skill_selection(
    session: Session,
    user_id: str,
    skill_ids: list[str] | None,
    *,
    secret_store: SecretStore | None = None,
    context_chars_used: int = 0,
) -> SkillSelection:
    if context_chars_used < 0:
        raise SkillSelectionInvalid("context usage cannot be negative")
    if skill_ids is None:
        skills = list(
            session.scalars(
                select(UserPromptSkill)
                .where(
                    UserPromptSkill.user_id == user_id,
                    UserPromptSkill.enabled.is_(True),
                    UserPromptSkill.default_enabled.is_(True),
                )
                .order_by(UserPromptSkill.created_at, UserPromptSkill.id)
            )
        )
    else:
        if len(skill_ids) != len(set(skill_ids)):
            raise SkillSelectionInvalid("skill IDs must be unique")
        owned = {
            skill.id: skill
            for skill in session.scalars(
                select(UserPromptSkill).where(
                    UserPromptSkill.user_id == user_id,
                    UserPromptSkill.id.in_(skill_ids or [""]),
                )
            )
        }
        if any(skill_id not in owned for skill_id in skill_ids):
            raise SkillSelectionNotFound("skill not found")
        skills = [owned[skill_id] for skill_id in skill_ids]
        if any(not skill.enabled for skill in skills):
            raise SkillSelectionInvalid("skill is disabled")
    if not skills:
        return SkillSelection()

    model_ids = {skill.model_profile_id for skill in skills if skill.model_profile_id}
    if len(model_ids) > 1:
        raise SkillSelectionInvalid("selected skills require conflicting models")
    model_profile_id = next(iter(model_ids), None)
    capability = None
    if model_profile_id is not None:
        model = session.scalar(
            select(UserModelProfile).where(
                UserModelProfile.id == model_profile_id,
                UserModelProfile.user_id == user_id,
            )
        )
        if model is None:
            raise SkillSelectionNotFound("skill model not found")
        if not model.enabled or model.capability not in {"chat", "reasoning"}:
            raise SkillSelectionInvalid("skill model must be enabled and text-capable")
        capability = model.capability

    sections = [f"[{skill.name}]\n{skill.instructions.strip()}" for skill in skills]
    body = "\n\n".join(sections)
    prefix = "--- BEGIN USER SKILL EXTENSIONS ---\n"
    suffix = "\n--- END USER SKILL EXTENSIONS ---"
    prompt = prefix + body + suffix
    if context_chars_used + len(prompt) > _TUTOR_REQUEST_CONTEXT_CHAR_BUDGET:
        raise SkillSelectionInvalid("selected skills exceed the prompt budget")
    secret_references = list(
        session.scalars(
            select(UserSecretReference).where(
                UserSecretReference.user_id == user_id,
                UserSecretReference.configured.is_(True),
            )
        )
    )
    if secret_references and secret_store is None:
        raise SkillSelectionInvalid("stored secrets cannot be checked")
    for reference in secret_references:
        try:
            secret_value = secret_store.get(reference.secret_ref)
        except Exception:
            raise SkillSelectionInvalid("stored secrets cannot be checked") from None
        if secret_value and secret_value in prompt:
            raise SkillSelectionInvalid("skill instructions contain a stored secret")
    return SkillSelection(
        skill_ids=tuple(skill.id for skill in skills),
        instruction_prompt=prompt,
        model_profile_id=model_profile_id,
        capability=capability,
    )


def embedding_profile_identity(profile: UserModelProfile) -> str:
    payload = json.dumps(
        {
            "provider": profile.provider,
            "base_url": canonicalize_provider_base_url(profile.base_url),
            "model": profile.model_name,
            "dimensions": profile.dimensions,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def environment_embedding_identity() -> str:
    client = build_embedding_client()
    payload = json.dumps(
        {
            "mode": getattr(client, "mode", None),
            "base_url": getattr(client, "base_url", None),
            "model": getattr(client, "model", None),
            "dimensions": getattr(client, "dimensions", 1536),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _is_deepseek_endpoint(base_url: str) -> bool:
    hostname = (urlsplit(base_url).hostname or "").lower()
    return hostname == "api.deepseek.com" or hostname.endswith(".deepseek.com")


def _embedding_profile_dimensions_valid(profile: UserModelProfile) -> bool:
    dimensions = profile.dimensions
    if dimensions is None or not 0 < dimensions <= 2048:
        return False
    hostname = (urlsplit(profile.base_url).hostname or "").casefold()
    return not (
        profile.model_name.casefold() == "embedding-3"
        and hostname.endswith("bigmodel.cn")
        and dimensions not in _ZHIPU_EMBEDDING_3_DIMENSIONS
    )


def run_model_test(
    session: Session,
    *,
    user_id: str,
    model_profile_id: str,
    secret_store: SecretStore | None,
) -> ModelTestOutcome:
    profile = session.scalar(
        select(UserModelProfile).where(
            UserModelProfile.id == model_profile_id,
            UserModelProfile.user_id == user_id,
        )
    )
    if profile is None:
        raise LookupError("model not found")
    outcome = ModelTestOutcome(status="success")
    try:
        client = RuntimeResolver(
            session, user_id=user_id, secret_store=secret_store
        ).resolve_profile(model_profile_id)
        if profile.capability == "chat":
            client.complete(
                role="model_test",
                prompt="Reply with OK.",
                max_output_tokens=1,
            )
        elif profile.capability == "reasoning":
            raw = client.complete(
                role="model_test",
                prompt='Return exactly this JSON object: {"ok":true}',
                max_output_tokens=32,
                json_output=True,
            )
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                raise RuntimeResolutionError("model_test.provider_response_invalid") from None
            if parsed != {"ok": True}:
                raise RuntimeResolutionError("model_test.provider_response_invalid")
        elif profile.capability == "vision":
            result = asyncio.run(
                client.analyze_image(
                    _TINY_PNG,
                    mime_type="image/png",
                    context=VisionContext(
                        filename="connection-test.png",
                        file_type=DocumentFileType.IMAGE,
                        page_number=1,
                        source_element=SourceElementType.IMAGE_FILE,
                        existing_ocr_text="",
                    ),
                )
            )
            if result.status is not VisionEnrichmentStatus.SUCCESS:
                raise RuntimeResolutionError("model_test.provider_failed")
        else:
            values = client.embed("connection test")
            if len(values) != profile.dimensions:
                raise RuntimeResolutionError("model_test.embedding_dimensions")
    except RuntimeResolutionError as exc:
        outcome = ModelTestOutcome(status="failed", code=exc.code)
    except EvaluationProviderError as exc:
        outcome = ModelTestOutcome(
            status="failed",
            code=_model_test_provider_code(exc.error_code),
        )
    except EmbeddingUnavailable:
        outcome = ModelTestOutcome(status="failed", code="model_test.provider_failed")
    except Exception:
        outcome = ModelTestOutcome(status="failed", code="model_test.provider_failed")
    profile.last_test_status = outcome.status
    profile.last_tested_at = datetime.now(timezone.utc)
    session.commit()
    return outcome


def _model_test_provider_code(error_code: str) -> str:
    if error_code in {"provider_request_failed", "provider_response_invalid"}:
        return f"model_test.{error_code}"
    if error_code.startswith("provider_http_"):
        raw_status = error_code.removeprefix("provider_http_")
        if raw_status.isdigit() and 400 <= int(raw_status) <= 599:
            return f"model_test.provider_http_{raw_status}"
    return "model_test.provider_failed"


__all__ = [
    "ModelTestOutcome",
    "RuntimeResolutionError",
    "RuntimeResolver",
    "SkillSelection",
    "SkillSelectionInvalid",
    "SkillSelectionNotFound",
    "StrictVisionClient",
    "embedding_profile_identity",
    "environment_embedding_identity",
    "resolve_skill_selection",
    "run_model_test",
]
