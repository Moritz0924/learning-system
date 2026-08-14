"""Bounded MCP discovery and invocation service."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import ipaddress
import json
import re
import socket
from typing import Any, AsyncContextManager, Callable, Literal, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import anyio
import httpx
from jsonschema import SchemaError, ValidationError, validators
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.secrets import SecretStore
from backend.app.models import UserMcpServer, UserMcpTool, UserSecretReference
from backend.app.services.provider_urls import (
    canonicalize_provider_base_url,
    has_sensitive_query_name,
)


_SENSITIVE_RESULT_KEY = re.compile(
    r"(?:api[_-]?key|auth(?:orization)?|credential|cookie|password|secret|token|(?:^|[_-])key(?:$|[_-]))",
    re.IGNORECASE,
)
_SAFE_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SAFE_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UNTRUSTED_INSTRUCTION = re.compile(
    r"ignore previous instructions|system prompt|developer message",
    re.IGNORECASE,
)
_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


class McpServiceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class McpConfigurationError(McpServiceError):
    def __init__(self) -> None:
        super().__init__("mcp.configuration_invalid")


class McpTrustRequired(McpServiceError):
    def __init__(self) -> None:
        super().__init__("mcp.trust_required")


class McpResourceNotFound(McpServiceError):
    def __init__(self) -> None:
        super().__init__("mcp.not_found")


class McpArgumentsInvalid(McpServiceError):
    def __init__(self) -> None:
        super().__init__("mcp.arguments_invalid")


class McpOutputTooLarge(McpServiceError):
    def __init__(self) -> None:
        super().__init__("mcp.output_too_large")


@dataclass(frozen=True)
class ToolApprovalRequired:
    server_id: str
    tool_name: str
    code: str = "mcp.approval_required"


@dataclass(frozen=True)
class McpOperationOutcome:
    status: Literal["success", "failed"]
    code: str | None = None
    tool_count: int | None = None


@dataclass(frozen=True)
class McpInvocationResult:
    value: Any
    truncated: bool


@dataclass
class McpConnection:
    server_id: str
    transport: Literal["streamable_http", "stdio"]
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    command: str | None = None
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    follow_redirects: bool = False
    shell: bool = False


class McpClientSession(Protocol):
    async def initialize(self) -> Any: ...

    async def list_tools(self, cursor: str | None = None) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


SessionFactory = Callable[[McpConnection], AsyncContextManager[McpClientSession]]
AddressResolver = Callable[[str], list[str]]


def _resolve_host_addresses(hostname: str) -> list[str]:
    try:
        return sorted(
            {
                entry[4][0]
                for entry in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
            }
        )
    except OSError:
        raise McpConfigurationError() from None


def _is_unsafe_target(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return True
    return parsed.is_link_local or parsed in _METADATA_ADDRESSES


def validate_mcp_http_url(
    url: str,
    *,
    resolver: AddressResolver = _resolve_host_addresses,
) -> str:
    try:
        canonical = canonicalize_provider_base_url(url)
        parts = urlsplit(canonical)
    except (TypeError, ValueError):
        raise McpConfigurationError() from None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise McpConfigurationError()
    if parts.username is not None or parts.password is not None or has_sensitive_query_name(canonical):
        raise McpConfigurationError()
    try:
        ipaddress.ip_address(parts.hostname)
        addresses = [parts.hostname]
    except ValueError:
        addresses = resolver(parts.hostname)
    if not addresses or any(_is_unsafe_target(address) for address in addresses):
        raise McpConfigurationError()
    return canonical


def stdio_trust_fingerprint(
    command: str,
    args: list[str],
    working_directory: str | None,
) -> str:
    payload = json.dumps(
        {
            "command": command,
            "args": args,
            "working_directory": working_directory,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def registry_tool_name(server_id: str, tool_name: str) -> str:
    server_identity = sha256(server_id.encode("utf-8")).hexdigest()[:12]
    tool_identity = sha256(tool_name.encode("utf-8")).hexdigest()[:8]
    readable = re.sub(r"[^A-Za-z0-9_.-]", "_", tool_name).strip("._-") or "tool"
    return f"mcp_{server_identity}_{readable[:96]}_{tool_identity}"


class _LimitedWriter(io.StringIO):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit

    def write(self, value: str) -> int:
        remaining = max(0, self.limit - self.tell())
        if remaining:
            super().write(value[:remaining])
        return len(value)


@asynccontextmanager
async def open_mcp_sdk_session(connection: McpConnection):
    async with AsyncExitStack() as stack:
        if connection.transport == "streamable_http":
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=connection.headers,
                    follow_redirects=False,
                    trust_env=False,
                )
            )
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(connection.url or "", http_client=client)
            )
        else:
            parameters = StdioServerParameters(
                command=connection.command or "",
                args=list(connection.args),
                env=dict(connection.env),
                cwd=connection.cwd,
            )
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(parameters, errlog=_LimitedWriter(65_536))
            )
        yield await stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=30),
            )
        )


class McpApplicationService:
    def __init__(
        self,
        session: Session,
        *,
        user_id: str,
        secret_store: SecretStore | None,
        session_factory: SessionFactory = open_mcp_sdk_session,
        resolver: AddressResolver = _resolve_host_addresses,
        startup_timeout_seconds: float = 10,
        call_timeout_seconds: float = 30,
        raw_output_byte_limit: int = 1_048_576,
        normalized_result_char_limit: int = 8_192,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.secret_store = secret_store
        self.session_factory = session_factory
        self.resolver = resolver
        self.startup_timeout_seconds = startup_timeout_seconds
        self.call_timeout_seconds = call_timeout_seconds
        self.raw_output_byte_limit = raw_output_byte_limit
        self.normalized_result_char_limit = normalized_result_char_limit

    def test_server(self, server_id: str) -> McpOperationOutcome:
        server = self._owned_server(server_id)
        try:
            self._run(server, self._test_operation)
        except McpServiceError as exc:
            return self._record_test(server, "failed", exc.code)
        except Exception:
            return self._record_test(server, "failed", "mcp.connection_failed")
        return self._record_test(server, "success", None)

    def discover_server(self, server_id: str) -> McpOperationOutcome:
        server = self._owned_server(server_id)
        try:
            tools = self._run(server, self._discover_operation)
            self._replace_catalog(server, tools)
        except McpServiceError as exc:
            self.session.rollback()
            return self._record_test(server, "failed", exc.code)
        except Exception:
            self.session.rollback()
            return self._record_test(server, "failed", "mcp.discovery_failed")
        server.last_test_status = "success"
        server.last_tested_at = datetime.now(timezone.utc)
        self.session.commit()
        return McpOperationOutcome(status="success", tool_count=len(tools))

    def invoke_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> McpInvocationResult | ToolApprovalRequired:
        server = self._owned_server(server_id)
        if not server.enabled:
            raise McpResourceNotFound()
        tool = self.session.scalar(
            select(UserMcpTool).where(
                UserMcpTool.mcp_server_id == server.id,
                UserMcpTool.name == tool_name,
                UserMcpTool.enabled.is_(True),
            )
        )
        if tool is None:
            raise McpResourceNotFound()
        self._validate_arguments(tool.input_schema_json, arguments)
        if tool.annotations_json.get("readOnlyHint") is not True:
            return ToolApprovalRequired(server_id=server.id, tool_name=tool.name)
        secret_values = tuple(self._secret_values(server.id).values())
        try:
            result = self._run(
                server,
                lambda client: self._call_operation(client, tool.name, arguments),
            )
        except McpServiceError:
            raise
        except Exception:
            raise McpServiceError("mcp.execution_failed") from None
        return self._normalize_result(result, secret_values=secret_values)

    def _owned_server(self, server_id: str) -> UserMcpServer:
        server = self.session.scalar(
            select(UserMcpServer).where(
                UserMcpServer.id == server_id,
                UserMcpServer.user_id == self.user_id,
            )
        )
        if server is None:
            raise McpResourceNotFound()
        return server

    def _record_test(
        self,
        server: UserMcpServer,
        status: Literal["success", "failed"],
        code: str | None,
    ) -> McpOperationOutcome:
        server.last_test_status = status
        server.last_tested_at = datetime.now(timezone.utc)
        self.session.commit()
        return McpOperationOutcome(status=status, code=code)

    def _connection(self, server: UserMcpServer) -> McpConnection:
        secrets = self._secret_values(server.id)
        if server.transport == "streamable_http":
            headers: dict[str, str] = {}
            for slot, value in secrets.items():
                if not slot.startswith("header:"):
                    continue
                name = slot.removeprefix("header:")
                if not _SAFE_HEADER_NAME.fullmatch(name) or "\r" in value or "\n" in value:
                    raise McpConfigurationError()
                headers[name] = value
            return McpConnection(
                server_id=server.id,
                transport="streamable_http",
                url=validate_mcp_http_url(server.url or "", resolver=self.resolver),
                headers=headers,
            )
        if server.transport != "stdio" or not server.command:
            raise McpConfigurationError()
        expected = stdio_trust_fingerprint(
            server.command,
            list(server.args_json or []),
            server.working_directory,
        )
        if server.trust_fingerprint != expected:
            raise McpTrustRequired()
        env = dict(server.env_json or {})
        if any(
            not isinstance(name, str)
            or not _SAFE_ENV_NAME.fullmatch(name)
            or not isinstance(value, str)
            or "\x00" in value
            for name, value in env.items()
        ):
            raise McpConfigurationError()
        for slot, value in secrets.items():
            if not slot.startswith("env:"):
                continue
            name = slot.removeprefix("env:")
            if not _SAFE_ENV_NAME.fullmatch(name) or "\x00" in value:
                raise McpConfigurationError()
            env[name] = value
        return McpConnection(
            server_id=server.id,
            transport="stdio",
            command=server.command,
            args=list(server.args_json or []),
            cwd=server.working_directory,
            env=env,
        )

    def _secret_values(self, server_id: str) -> dict[str, str]:
        references = self.session.scalars(
            select(UserSecretReference).where(
                UserSecretReference.user_id == self.user_id,
                UserSecretReference.owner_type == "mcp_server",
                UserSecretReference.owner_id == server_id,
                UserSecretReference.configured.is_(True),
            )
        ).all()
        if not references:
            return {}
        if self.secret_store is None:
            raise McpConfigurationError()
        values: dict[str, str] = {}
        for reference in references:
            try:
                value = self.secret_store.get(reference.secret_ref)
            except Exception:
                raise McpConfigurationError() from None
            if not value:
                raise McpConfigurationError()
            values[reference.slot] = value
        return values

    def _run(self, server: UserMcpServer, operation: Callable[[McpClientSession], Any]):
        connection = self._connection(server)

        async def execute():
            async with AsyncExitStack() as stack:
                try:
                    with anyio.fail_after(self.startup_timeout_seconds):
                        client = await stack.enter_async_context(self.session_factory(connection))
                        await client.initialize()
                    with anyio.fail_after(self.call_timeout_seconds):
                        return await operation(client)
                except TimeoutError:
                    raise McpServiceError("mcp.timeout") from None

        try:
            return anyio.run(execute)
        finally:
            connection.headers.clear()
            connection.env.clear()

    async def _test_operation(self, client: McpClientSession):
        page = await client.list_tools()
        self._serialized_size(page.model_dump(mode="json", by_alias=True, exclude_none=True))
        return page

    async def _discover_operation(self, client: McpClientSession) -> list[Any]:
        tools: list[Any] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        output_bytes = 0
        while True:
            page = await client.list_tools(cursor)
            output_bytes += self._serialized_size(
                page.model_dump(mode="json", by_alias=True, exclude_none=True)
            )
            if output_bytes > self.raw_output_byte_limit:
                raise McpOutputTooLarge()
            tools.extend(page.tools)
            cursor = page.nextCursor
            if cursor is None:
                return tools
            if cursor in seen_cursors:
                raise McpServiceError("mcp.pagination_invalid")
            seen_cursors.add(cursor)

    @staticmethod
    async def _call_operation(
        client: McpClientSession,
        tool_name: str,
        arguments: dict[str, Any],
    ):
        return await client.call_tool(tool_name, arguments)

    def _replace_catalog(self, server: UserMcpServer, discovered: list[Any]) -> None:
        existing = {
            tool.name: tool
            for tool in self.session.scalars(
                select(UserMcpTool).where(UserMcpTool.mcp_server_id == server.id)
            )
        }
        names: set[str] = set()
        now = datetime.now(timezone.utc)
        for source in discovered:
            name = source.name.strip()
            if not name or len(name) > 128 or name in names:
                raise McpServiceError("mcp.catalog_invalid")
            names.add(name)
            tool = existing.get(name)
            if tool is None:
                tool = UserMcpTool(
                    id=str(uuid4()),
                    mcp_server_id=server.id,
                    name=name,
                    enabled=True,
                )
                self.session.add(tool)
            annotations = (
                source.annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
                if source.annotations is not None
                else {}
            )
            tool.title = source.title[:255] if source.title else None
            tool.description = source.description or ""
            tool.input_schema_json = source.inputSchema
            tool.annotations_json = annotations
            tool.discovered_at = now
        for name, tool in existing.items():
            if name not in names:
                self.session.delete(tool)

    @staticmethod
    def _validate_arguments(schema: dict, arguments: dict[str, Any]) -> None:
        try:
            validator_class = validators.validator_for(schema)
            validator_class.check_schema(schema)
            validator_class(schema).validate(arguments)
        except (SchemaError, ValidationError, TypeError):
            raise McpArgumentsInvalid() from None

    def _normalize_result(
        self,
        result: Any,
        *,
        secret_values: tuple[str, ...] = (),
    ) -> McpInvocationResult:
        if getattr(result, "isError", False):
            raise McpServiceError("mcp.tool_failed")
        if result.structuredContent is not None:
            raw: Any = result.structuredContent
        else:
            raw = [
                item.model_dump(mode="json", by_alias=True, exclude_none=True)
                for item in result.content
            ]
        self._serialized_size(raw)
        value = _sanitize_result(raw, secret_values=secret_values)
        normalized = json.dumps(value, ensure_ascii=False, default=str)
        if len(normalized) <= self.normalized_result_char_limit:
            return McpInvocationResult(value=value, truncated=False)
        return McpInvocationResult(
            value=normalized[: self.normalized_result_char_limit],
            truncated=True,
        )

    def _serialized_size(self, value: Any) -> int:
        try:
            size = len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
        except Exception:
            raise McpServiceError("mcp.output_invalid") from None
        if size > self.raw_output_byte_limit:
            raise McpOutputTooLarge()
        return size


def _sanitize_result(value: Any, *, secret_values: tuple[str, ...] = ()) -> Any:
    if isinstance(value, str):
        sanitized = _UNTRUSTED_INSTRUCTION.sub("[filtered untrusted instruction]", value)
        for secret in secret_values:
            sanitized = sanitized.replace(secret, "[redacted]")
        return sanitized
    if isinstance(value, list):
        return [_sanitize_result(item, secret_values=secret_values) for item in value]
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if _SENSITIVE_RESULT_KEY.search(str(key))
            else _sanitize_result(item, secret_values=secret_values)
            for key, item in value.items()
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


__all__ = [
    "McpApplicationService",
    "McpArgumentsInvalid",
    "McpConfigurationError",
    "McpConnection",
    "McpInvocationResult",
    "McpOperationOutcome",
    "McpResourceNotFound",
    "McpServiceError",
    "McpTrustRequired",
    "ToolApprovalRequired",
    "open_mcp_sdk_session",
    "registry_tool_name",
    "stdio_trust_fingerprint",
    "validate_mcp_http_url",
]
