from __future__ import annotations

import re
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.application.config_service import embedding_profile_identity, run_model_test
from backend.app.application.mcp_service import (
    McpApplicationService,
    McpOperationOutcome,
    McpServiceError,
    SessionFactory,
    open_mcp_sdk_session,
)
from backend.app.application.embedding_reindex_service import enqueue_embedding_reindex_events
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.infrastructure.secrets import SecretStore, SecretStoreUnavailable, WindowsCredentialManagerSecretStore
from backend.app.services.provider_urls import (
    canonicalize_provider_base_url,
    has_sensitive_query_name,
)
from backend.app.models import (
    UserCapabilityBinding,
    User,
    UserMcpServer,
    UserMcpTool,
    UserModelProfile,
    UserPromptSkill,
    UserSecretReference,
)


router = APIRouter(prefix="/api/config", tags=["configuration"])

Capability = Literal["chat", "reasoning", "vision", "embedding"]
_SENSITIVE_ENV_NAME = re.compile(
    r"(?:api[_-]?key|auth(?:orization)?|credential|password|secret|token|(?:^|[_-])key(?:$|[_-]))",
    re.IGNORECASE,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ModelProfileWrite(_StrictModel):
    name: str = Field(min_length=1)
    capability: Capability
    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: HttpUrl
    model_name: str = Field(min_length=1)
    dimensions: int | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def require_supported_embedding_dimensions(self) -> "ModelProfileWrite":
        if self.capability == "embedding" and self.dimensions != 1536:
            raise ValueError("embedding dimensions must be 1536")
        if self.capability != "embedding" and self.dimensions is not None:
            raise ValueError("dimensions are only valid for embedding models")
        return self


class ModelProfileResponse(_StrictModel):
    id: str
    name: str
    capability: Capability
    provider: Literal["openai_compatible"]
    base_url: str
    model_name: str
    dimensions: int | None
    enabled: bool
    last_test_status: str | None


class ModelProfileListResponse(_StrictModel):
    models: list[ModelProfileResponse]


class ModelTestResponse(_StrictModel):
    status: Literal["success", "failed"]
    code: str | None = None


class SecretWrite(_StrictModel):
    value: str

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    @model_validator(mode="after")
    def reject_blank_value(self) -> "SecretWrite":
        if not self.value.strip():
            raise ValueError("secret value must not be blank")
        return self


class SecretStatusResponse(_StrictModel):
    configured: bool
    masked_value: str


class BindingWrite(_StrictModel):
    model_profile_id: str


class BindingResponse(_StrictModel):
    id: str
    capability: Capability
    model_profile_id: str


class BindingListResponse(_StrictModel):
    bindings: list[BindingResponse]


class SkillWrite(_StrictModel):
    name: str
    description: str = ""
    instructions: str = Field(min_length=1, max_length=4000)
    enabled: bool = True
    default_enabled: bool = False
    model_profile_id: str | None = None


class SkillResponse(SkillWrite):
    id: str


class SkillListResponse(_StrictModel):
    skills: list[SkillResponse]


class McpServerWrite(_StrictModel):
    name: str
    transport: Literal["streamable_http", "stdio"]
    url: HttpUrl | None = None
    command: str | None = None
    args: list[str] = []
    working_directory: str | None = None
    env: dict[str, str] = {}
    enabled: bool = True

    @model_validator(mode="after")
    def validate_transport_shape(self) -> "McpServerWrite":
        if self.transport == "streamable_http":
            if self.url is None or self.command or self.args or self.working_directory:
                raise ValueError("streamable_http requires url only")
        elif not self.command:
            raise ValueError("stdio requires command")
        elif self.url is not None:
            raise ValueError("stdio does not accept url")
        return self


class McpServerResponse(_StrictModel):
    id: str
    name: str
    transport: Literal["streamable_http", "stdio"]
    url: str | None
    command: str | None
    args: list[str]
    working_directory: str | None
    env: dict[str, str]
    enabled: bool
    trust_fingerprint: str | None
    trusted_at: str | None
    last_test_status: str | None


class McpServerListResponse(_StrictModel):
    mcp_servers: list[McpServerResponse]


class EnabledWrite(_StrictModel):
    enabled: bool


class McpToolResponse(_StrictModel):
    id: str
    name: str
    title: str | None
    description: str
    enabled: bool


class McpOperationResponse(_StrictModel):
    status: Literal["success", "failed"]
    code: str | None = None
    tool_count: int | None = None


def get_secret_store() -> SecretStore | None:
    try:
        return WindowsCredentialManagerSecretStore()
    except SecretStoreUnavailable:
        return None


def get_mcp_session_factory() -> SessionFactory:
    return open_mcp_sdk_session


def _model_response(model: UserModelProfile) -> ModelProfileResponse:
    return ModelProfileResponse(
        id=model.id,
        name=model.name,
        capability=model.capability,
        provider=model.provider,
        base_url=model.base_url,
        model_name=model.model_name,
        dimensions=model.dimensions,
        enabled=model.enabled,
        last_test_status=model.last_test_status,
    )


def _owned_model(session: Session, user_id: str, model_id: str) -> UserModelProfile:
    model = session.scalar(
        select(UserModelProfile).where(
            UserModelProfile.id == model_id,
            UserModelProfile.user_id == user_id,
        )
    )
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model not found")
    return model


def _owned_skill(session: Session, user_id: str, skill_id: str) -> UserPromptSkill:
    skill = session.scalar(select(UserPromptSkill).where(UserPromptSkill.id == skill_id, UserPromptSkill.user_id == user_id))
    if skill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="skill not found")
    return skill


def _validated_skill_model(
    session: Session, user_id: str, model_profile_id: str | None
) -> None:
    if model_profile_id is None:
        return
    model = _owned_model(session, user_id, model_profile_id)
    if not model.enabled or model.capability not in {"chat", "reasoning"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="skill model must be enabled and text-capable",
        )


def _lock_user_configuration(session: Session, user_id: str) -> None:
    session.scalar(select(User.id).where(User.id == user_id).with_for_update())


def _owned_mcp_server(session: Session, user_id: str, server_id: str) -> UserMcpServer:
    server = session.scalar(select(UserMcpServer).where(UserMcpServer.id == server_id, UserMcpServer.user_id == user_id))
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return server


def _binding_response(binding: UserCapabilityBinding) -> BindingResponse:
    return BindingResponse(id=binding.id, capability=binding.capability, model_profile_id=binding.model_profile_id)


def _skill_response(skill: UserPromptSkill) -> SkillResponse:
    return SkillResponse(
        id=skill.id, name=skill.name, description=skill.description, instructions=skill.instructions,
        enabled=skill.enabled, default_enabled=skill.default_enabled, model_profile_id=skill.model_profile_id,
    )


def _mcp_server_response(server: UserMcpServer) -> McpServerResponse:
    return McpServerResponse(
        id=server.id, name=server.name, transport=server.transport, url=server.url, command=server.command,
        args=server.args_json, working_directory=server.working_directory, env=server.env_json, enabled=server.enabled,
        trust_fingerprint=server.trust_fingerprint,
        trusted_at=server.trusted_at.isoformat() if server.trusted_at else None,
        last_test_status=server.last_test_status,
    )


def _mcp_tool_response(tool: UserMcpTool) -> McpToolResponse:
    return McpToolResponse(id=tool.id, name=tool.name, title=tool.title, description=tool.description, enabled=tool.enabled)


def _validated_remote_url(url: HttpUrl) -> str:
    if url.username is not None or url.password is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="URL must not contain credentials")
    raw_url = str(url)
    if has_sensitive_query_name(raw_url):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="URL must not contain secret query parameters")
    try:
        return canonicalize_provider_base_url(raw_url)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="URL is invalid") from None


def _validate_mcp_server_secrets(payload: McpServerWrite) -> None:
    if payload.url is not None:
        _validated_remote_url(payload.url)
    if any(_SENSITIVE_ENV_NAME.search(name) for name in payload.env):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="MCP secret values must use the secrets endpoint")


@router.post("/models", response_model=ModelProfileResponse, status_code=status.HTTP_201_CREATED)
def create_model(
    payload: ModelProfileWrite,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> ModelProfileResponse:
    model = UserModelProfile(
        id=str(uuid4()),
        user_id=principal.user_id,
        name=payload.name,
        capability=payload.capability,
        provider=payload.provider,
        base_url=_validated_remote_url(payload.base_url),
        model_name=payload.model_name,
        dimensions=payload.dimensions,
        enabled=payload.enabled,
    )
    session.add(model)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model name already exists") from exc
    return _model_response(model)


@router.get("/models", response_model=ModelProfileListResponse)
def list_models(
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> ModelProfileListResponse:
    models = session.scalars(
        select(UserModelProfile)
        .where(UserModelProfile.user_id == principal.user_id)
        .order_by(UserModelProfile.created_at, UserModelProfile.id)
    ).all()
    return ModelProfileListResponse(models=[_model_response(model) for model in models])


@router.get("/models/{model_id}", response_model=ModelProfileResponse)
def get_model(
    model_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> ModelProfileResponse:
    return _model_response(_owned_model(session, principal.user_id, model_id))


@router.post("/models/{model_id}/test", response_model=ModelTestResponse)
def test_model(
    model_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
) -> ModelTestResponse:
    _owned_model(session, principal.user_id, model_id)
    outcome = run_model_test(
        session,
        user_id=principal.user_id,
        model_profile_id=model_id,
        secret_store=store,
    )
    return ModelTestResponse(status=outcome.status, code=outcome.code)


@router.put("/models/{model_id}", response_model=ModelProfileResponse)
def update_model(
    model_id: str,
    payload: ModelProfileWrite,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> ModelProfileResponse:
    model = _owned_model(session, principal.user_id, model_id)
    if model.capability == "embedding" or payload.capability == "embedding":
        _lock_user_configuration(session, principal.user_id)
        session.refresh(model)
    original_embedding_identity = (
        embedding_profile_identity(model) if model.capability == "embedding" else None
    )
    was_enabled = model.enabled
    if payload.capability != model.capability and session.scalar(
        select(UserCapabilityBinding.id).where(
            UserCapabilityBinding.user_id == principal.user_id,
            UserCapabilityBinding.model_profile_id == model_id,
        )
    ) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="cannot change capability while model is bound")
    bound_embedding = session.scalar(
        select(UserCapabilityBinding.id).where(
            UserCapabilityBinding.user_id == principal.user_id,
            UserCapabilityBinding.capability == "embedding",
            UserCapabilityBinding.model_profile_id == model_id,
        )
    ) is not None
    if bound_embedding:
        _lock_user_configuration(session, principal.user_id)
    model.name = payload.name
    model.capability = payload.capability
    model.provider = payload.provider
    model.base_url = _validated_remote_url(payload.base_url)
    model.model_name = payload.model_name
    model.dimensions = payload.dimensions
    model.enabled = payload.enabled
    try:
        current_embedding_identity = (
            embedding_profile_identity(model) if model.capability == "embedding" else None
        )
        if bound_embedding and (
            current_embedding_identity != original_embedding_identity
            or (not was_enabled and model.enabled)
        ):
            session.flush()
            enqueue_embedding_reindex_events(
                session,
                user_id=principal.user_id,
                model_profile_id=model.id,
                change_id=str(uuid4()),
                queued_profile_identity=current_embedding_identity,
            )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model name already exists") from exc
    return _model_response(model)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(
    model_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
) -> None:
    model = _owned_model(session, principal.user_id, model_id)
    if session.scalar(select(UserCapabilityBinding.id).where(UserCapabilityBinding.user_id == principal.user_id, UserCapabilityBinding.model_profile_id == model_id)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model is referenced by a binding")
    if session.scalar(select(UserPromptSkill.id).where(UserPromptSkill.user_id == principal.user_id, UserPromptSkill.model_profile_id == model_id)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model is referenced by a skill")
    references = session.scalars(
        select(UserSecretReference).where(
            UserSecretReference.user_id == principal.user_id,
            UserSecretReference.owner_type == "model",
            UserSecretReference.owner_id == model_id,
        )
    ).all()
    session.delete(model)
    for reference in references:
        session.delete(reference)
    session.commit()
    if store is not None:
        for reference in references:
            _best_effort_delete(store, reference.secret_ref)


def _mask_secret(value: str) -> str:
    return "********"


def _write_secret(
    *,
    session: Session,
    store: SecretStore,
    user_id: str,
    owner_type: str,
    owner_id: str,
    slot: str,
    value: str,
) -> SecretStatusResponse:
    existing = session.scalar(
        select(UserSecretReference).where(
            UserSecretReference.user_id == user_id,
            UserSecretReference.owner_type == owner_type,
            UserSecretReference.owner_id == owner_id,
            UserSecretReference.slot == slot,
        )
    )
    new_ref = f"learning-system:{owner_type}:{user_id}:{owner_id}:{slot}:{uuid4()}"
    try:
        store.put(new_ref, value)
    except Exception:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="secret store is unavailable") from None
    old_ref = existing.secret_ref if existing is not None else None
    if existing is None:
        existing = UserSecretReference(
            id=str(uuid4()), user_id=user_id, owner_type=owner_type, owner_id=owner_id,
            slot=slot, secret_ref=new_ref, configured=True, masked_value=_mask_secret(value),
        )
        session.add(existing)
    else:
        existing.secret_ref = new_ref
        existing.configured = True
        existing.masked_value = _mask_secret(value)
    try:
        session.commit()
    except Exception:
        session.rollback()
        _best_effort_delete(store, new_ref)
        raise
    if old_ref is not None:
        _best_effort_delete(store, old_ref)
    return SecretStatusResponse(configured=existing.configured, masked_value=existing.masked_value)


def _delete_secret(*, session: Session, store: SecretStore | None, reference: UserSecretReference) -> None:
    secret_ref = reference.secret_ref
    session.delete(reference)
    session.commit()
    if store is not None:
        _best_effort_delete(store, secret_ref)


def _best_effort_delete(store: SecretStore, secret_ref: str) -> None:
    try:
        store.delete(secret_ref)
    except Exception:
        pass


@router.put("/models/{model_id}/secret", response_model=SecretStatusResponse)
def put_model_secret(
    model_id: str,
    payload: SecretWrite,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
) -> SecretStatusResponse:
    _owned_model(session, principal.user_id, model_id)
    if store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="secret store is unavailable")
    return _write_secret(
        session=session, store=store, user_id=principal.user_id, owner_type="model",
        owner_id=model_id, slot="api_key", value=payload.value,
    )


@router.delete("/models/{model_id}/secret", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_secret(
    model_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
) -> None:
    _owned_model(session, principal.user_id, model_id)
    reference = session.scalar(
        select(UserSecretReference).where(
            UserSecretReference.user_id == principal.user_id,
            UserSecretReference.owner_type == "model",
            UserSecretReference.owner_id == model_id,
            UserSecretReference.slot == "api_key",
        )
    )
    if reference is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="secret not found")
    _delete_secret(session=session, store=store, reference=reference)


@router.get("/bindings", response_model=BindingListResponse)
def list_bindings(principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> BindingListResponse:
    bindings = session.scalars(select(UserCapabilityBinding).where(UserCapabilityBinding.user_id == principal.user_id).order_by(UserCapabilityBinding.capability)).all()
    return BindingListResponse(bindings=[_binding_response(binding) for binding in bindings])


@router.get("/bindings/{capability}", response_model=BindingResponse)
def get_binding(capability: Capability, principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> BindingResponse:
    binding = session.scalar(select(UserCapabilityBinding).where(UserCapabilityBinding.user_id == principal.user_id, UserCapabilityBinding.capability == capability))
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="binding not found")
    return _binding_response(binding)


@router.put("/bindings/{capability}", response_model=BindingResponse)
def put_binding(capability: Capability, payload: BindingWrite, principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> BindingResponse:
    if capability == "embedding":
        _lock_user_configuration(session, principal.user_id)
    model = _owned_model(session, principal.user_id, payload.model_profile_id)
    if model.capability != capability:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="model capability does not match binding")
    if not model.enabled:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="model profile is disabled")
    binding = session.scalar(select(UserCapabilityBinding).where(UserCapabilityBinding.user_id == principal.user_id, UserCapabilityBinding.capability == capability))
    changed = binding is None or binding.model_profile_id != model.id
    if binding is None:
        binding = UserCapabilityBinding(id=str(uuid4()), user_id=principal.user_id, capability=capability, model_profile_id=model.id)
        session.add(binding)
    else:
        binding.model_profile_id = model.id
    if capability == "embedding" and changed:
        enqueue_embedding_reindex_events(
            session,
            user_id=principal.user_id,
            model_profile_id=model.id,
            change_id=str(uuid4()),
        )
    session.commit()
    return _binding_response(binding)


@router.delete("/bindings/{capability}", status_code=status.HTTP_204_NO_CONTENT)
def delete_binding(capability: Capability, principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> None:
    if capability == "embedding":
        _lock_user_configuration(session, principal.user_id)
    binding = session.scalar(select(UserCapabilityBinding).where(UserCapabilityBinding.user_id == principal.user_id, UserCapabilityBinding.capability == capability))
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="binding not found")
    session.delete(binding)
    if capability == "embedding":
        enqueue_embedding_reindex_events(
            session,
            user_id=principal.user_id,
            model_profile_id=None,
            change_id=str(uuid4()),
        )
    session.commit()


@router.post("/skills", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
def create_skill(payload: SkillWrite, principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> SkillResponse:
    _validated_skill_model(session, principal.user_id, payload.model_profile_id)
    skill = UserPromptSkill(id=str(uuid4()), user_id=principal.user_id, **payload.model_dump())
    session.add(skill)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="skill name already exists") from exc
    return _skill_response(skill)


@router.get("/skills", response_model=SkillListResponse)
def list_skills(principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> SkillListResponse:
    skills = session.scalars(select(UserPromptSkill).where(UserPromptSkill.user_id == principal.user_id).order_by(UserPromptSkill.created_at, UserPromptSkill.id)).all()
    return SkillListResponse(skills=[_skill_response(skill) for skill in skills])


@router.get("/skills/{skill_id}", response_model=SkillResponse)
def get_skill(skill_id: str, principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> SkillResponse:
    return _skill_response(_owned_skill(session, principal.user_id, skill_id))


@router.put("/skills/{skill_id}", response_model=SkillResponse)
def update_skill(skill_id: str, payload: SkillWrite, principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> SkillResponse:
    _validated_skill_model(session, principal.user_id, payload.model_profile_id)
    skill = _owned_skill(session, principal.user_id, skill_id)
    for field, value in payload.model_dump().items():
        setattr(skill, field, value)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="skill name already exists") from exc
    return _skill_response(skill)


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(skill_id: str, principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> None:
    session.delete(_owned_skill(session, principal.user_id, skill_id))
    session.commit()


def _apply_mcp_server_payload(server: UserMcpServer, payload: McpServerWrite) -> None:
    command_or_args_changed = server.command != payload.command or server.args_json != payload.args
    server.name = payload.name
    server.transport = payload.transport
    server.url = str(payload.url) if payload.url is not None else None
    server.command = payload.command
    server.args_json = payload.args
    server.working_directory = payload.working_directory
    server.env_json = payload.env
    server.enabled = payload.enabled
    if command_or_args_changed:
        server.trust_fingerprint = None
        server.trusted_at = None


@router.post("/mcp-servers", response_model=McpServerResponse, status_code=status.HTTP_201_CREATED)
def create_mcp_server(payload: McpServerWrite, principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> McpServerResponse:
    _validate_mcp_server_secrets(payload)
    server = UserMcpServer(id=str(uuid4()), user_id=principal.user_id, name=payload.name, transport=payload.transport)
    _apply_mcp_server_payload(server, payload)
    session.add(server)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MCP server name already exists") from exc
    return _mcp_server_response(server)


@router.get("/mcp-servers", response_model=McpServerListResponse)
def list_mcp_servers(principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> McpServerListResponse:
    servers = session.scalars(select(UserMcpServer).where(UserMcpServer.user_id == principal.user_id).order_by(UserMcpServer.created_at, UserMcpServer.id)).all()
    return McpServerListResponse(mcp_servers=[_mcp_server_response(server) for server in servers])


@router.get("/mcp-servers/{server_id}", response_model=McpServerResponse)
def get_mcp_server(server_id: str, principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> McpServerResponse:
    return _mcp_server_response(_owned_mcp_server(session, principal.user_id, server_id))


@router.put("/mcp-servers/{server_id}", response_model=McpServerResponse)
def update_mcp_server(server_id: str, payload: McpServerWrite, principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> McpServerResponse:
    _validate_mcp_server_secrets(payload)
    server = _owned_mcp_server(session, principal.user_id, server_id)
    _apply_mcp_server_payload(server, payload)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MCP server name already exists") from exc
    return _mcp_server_response(server)


@router.delete("/mcp-servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mcp_server(
    server_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
) -> None:
    _owned_mcp_server(session, principal.user_id, server_id)
    references = session.scalars(select(UserSecretReference).where(UserSecretReference.user_id == principal.user_id, UserSecretReference.owner_type == "mcp_server", UserSecretReference.owner_id == server_id)).all()
    session.delete(_owned_mcp_server(session, principal.user_id, server_id))
    for reference in references:
        session.delete(reference)
    session.commit()
    if store is not None:
        for reference in references:
            _best_effort_delete(store, reference.secret_ref)


@router.put("/mcp-servers/{server_id}/secrets/{slot}", response_model=SecretStatusResponse)
def put_mcp_server_secret(
    server_id: str,
    slot: str,
    payload: SecretWrite,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
) -> SecretStatusResponse:
    _owned_mcp_server(session, principal.user_id, server_id)
    if store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="secret store is unavailable")
    return _write_secret(
        session=session, store=store, user_id=principal.user_id, owner_type="mcp_server",
        owner_id=server_id, slot=slot, value=payload.value,
    )


@router.delete("/mcp-servers/{server_id}/secrets/{slot}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mcp_server_secret(
    server_id: str,
    slot: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
) -> None:
    _owned_mcp_server(session, principal.user_id, server_id)
    reference = session.scalar(
        select(UserSecretReference).where(
            UserSecretReference.user_id == principal.user_id,
            UserSecretReference.owner_type == "mcp_server",
            UserSecretReference.owner_id == server_id,
            UserSecretReference.slot == slot,
        )
    )
    if reference is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="secret not found")
    _delete_secret(session=session, store=store, reference=reference)


def _mcp_operation_response(outcome: McpOperationOutcome) -> McpOperationResponse:
    return McpOperationResponse(
        status=outcome.status,
        code=outcome.code,
        tool_count=outcome.tool_count,
    )


@router.post("/mcp-servers/{server_id}/test", response_model=McpOperationResponse)
def test_mcp_server(
    server_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
    session_factory: SessionFactory = Depends(get_mcp_session_factory),
) -> McpOperationResponse:
    _owned_mcp_server(session, principal.user_id, server_id)
    try:
        outcome = McpApplicationService(
            session,
            user_id=principal.user_id,
            secret_store=store,
            session_factory=session_factory,
        ).test_server(server_id)
    except McpServiceError as exc:
        outcome = McpOperationOutcome(status="failed", code=exc.code)
    return _mcp_operation_response(outcome)


@router.post("/mcp-servers/{server_id}/discover", response_model=McpOperationResponse)
def discover_mcp_server(
    server_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    store: SecretStore | None = Depends(get_secret_store),
    session_factory: SessionFactory = Depends(get_mcp_session_factory),
) -> McpOperationResponse:
    _owned_mcp_server(session, principal.user_id, server_id)
    try:
        outcome = McpApplicationService(
            session,
            user_id=principal.user_id,
            secret_store=store,
            session_factory=session_factory,
        ).discover_server(server_id)
    except McpServiceError as exc:
        outcome = McpOperationOutcome(status="failed", code=exc.code)
    return _mcp_operation_response(outcome)


@router.put("/mcp-servers/{server_id}/tools/{tool_name}", response_model=McpToolResponse)
@router.patch("/mcp-servers/{server_id}/tools/{tool_name}", response_model=McpToolResponse)
def set_mcp_tool_enabled(
    server_id: str,
    tool_name: str,
    payload: EnabledWrite,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> McpToolResponse:
    _owned_mcp_server(session, principal.user_id, server_id)
    tool = session.scalar(select(UserMcpTool).where(UserMcpTool.mcp_server_id == server_id, UserMcpTool.name == tool_name))
    if tool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP tool not found")
    tool.enabled = payload.enabled
    session.commit()
    return _mcp_tool_response(tool)
