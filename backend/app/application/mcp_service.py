"""Bounded MCP discovery and invocation service."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import ipaddress
import json
import logging
import os
import re
import socket
from typing import Any, AsyncContextManager, Callable, Literal, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import anyio
import httpx
from jsonschema import SchemaError, ValidationError, validators
from mcp import ClientSession, types
from mcp.client.stdio import (
    PROCESS_TERMINATION_TIMEOUT,
    StdioServerParameters,
    _create_platform_compatible_process,
    _get_executable_command,
    _terminate_process_tree,
    get_default_environment,
)
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
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


@dataclass(frozen=True)
class McpTrustOutcome:
    trust_fingerprint: str
    trusted_at: datetime


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
    pinned_address: str | None = None
    server_hostname: str | None = None
    port: int | None = None
    raw_output_byte_limit: int = 1_048_576
    startup_timeout_seconds: float = 10
    output_limit_exceeded: bool = False


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


def _normalized_address(address: str):
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        raise McpConfigurationError() from None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def _is_unsafe_target(address: str) -> bool:
    try:
        parsed = _normalized_address(address)
    except McpConfigurationError:
        return True
    return parsed.is_link_local or parsed in _METADATA_ADDRESSES


@dataclass(frozen=True)
class _ResolvedHttpTarget:
    url: str
    hostname: str
    port: int
    pinned_address: str


def _resolve_http_target(url: str, resolver: AddressResolver) -> _ResolvedHttpTarget:
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
    return _ResolvedHttpTarget(
        url=canonical,
        hostname=parts.hostname,
        port=parts.port or (443 if parts.scheme == "https" else 80),
        pinned_address=str(_normalized_address(addresses[0])),
    )


def validate_mcp_http_url(
    url: str,
    *,
    resolver: AddressResolver = _resolve_host_addresses,
) -> str:
    return _resolve_http_target(url, resolver).url


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
    identity = sha256(f"{server_id}\0{tool_name}".encode("utf-8")).hexdigest()
    readable = re.sub(r"[^A-Za-z0-9_.-]", "_", tool_name).strip("._-") or "tool"
    return f"mcp_{identity}_{readable[:58]}"


class _BoundedAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, limit: int, on_overflow) -> None:
        self.stream = stream
        self.limit = limit
        self.on_overflow = on_overflow

    async def __aiter__(self):
        received = 0
        async for chunk in self.stream:
            received += len(chunk)
            if received > self.limit:
                self.on_overflow()
                raise McpOutputTooLarge()
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class _PinnedHttpTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        pinned_address: str,
        server_hostname: str,
        port: int,
        byte_limit: int,
        on_overflow=lambda: None,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.pinned_address = pinned_address
        self.server_hostname = server_hostname
        self.port = port
        self.byte_limit = byte_limit
        self.on_overflow = on_overflow
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        headers = httpx.Headers(request.headers)
        host = self.server_hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        default_port = (request.url.scheme == "https" and self.port == 443) or (
            request.url.scheme == "http" and self.port == 80
        )
        headers["Host"] = host if default_port else f"{host}:{self.port}"
        pinned_request = httpx.Request(
            request.method,
            request.url.copy_with(host=self.pinned_address),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": self.server_hostname},
        )
        response = await self.inner.handle_async_request(pinned_request)
        if response.headers.get("Content-Encoding", "identity").strip().lower() not in {
            "",
            "identity",
        }:
            self.on_overflow()
            await response.aclose()
            raise McpOutputTooLarge()
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=_BoundedAsyncByteStream(
                response.stream, self.byte_limit, self.on_overflow
            ),
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


class _DropTransportLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        path = record.pathname.replace("\\", "/").lower()
        return not any(
            marker in path
            for marker in (
                "/site-packages/mcp/",
                "/site-packages/httpx/",
                "/site-packages/httpcore/",
            )
        )


@contextmanager
def _suppress_transport_logs():
    prefixes = ("mcp.client", "httpx", "httpcore")
    loggers = [
        value
        for name, value in logging.Logger.manager.loggerDict.items()
        if isinstance(value, logging.Logger) and name.startswith(prefixes)
    ]
    loggers.extend(logging.getLogger(name) for name in prefixes)
    loggers.append(logging.getLogger())
    filter_ = _DropTransportLogs()
    for logger in set(loggers):
        logger.addFilter(filter_)
    try:
        yield
    finally:
        for logger in set(loggers):
            logger.removeFilter(filter_)


@asynccontextmanager
async def _bounded_stdio_client(
    server: StdioServerParameters,
    *,
    byte_limit: int,
    startup_timeout_seconds: float,
    on_overflow,
    errlog: io.TextIOBase,
):
    read_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_reader = anyio.create_memory_object_stream(0)
    try:
        with anyio.fail_after(startup_timeout_seconds):
            process = await _create_platform_compatible_process(
                command=_get_executable_command(server.command),
                args=server.args,
                env={**get_default_environment(), **(server.env or {})},
                errlog=errlog,
                cwd=server.cwd,
            )
    except OSError:
        await read_stream.aclose()
        await write_stream.aclose()
        await read_writer.aclose()
        await write_reader.aclose()
        raise

    async def stdout_reader() -> None:
        assert process.stdout is not None
        buffer = b""
        async with read_writer:
            while True:
                try:
                    chunk = await process.stdout.receive(65_536)
                except anyio.EndOfStream:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if len(line) > byte_limit:
                        on_overflow()
                        await read_writer.send(McpOutputTooLarge())
                        return
                    try:
                        message = types.JSONRPCMessage.model_validate_json(line)
                    except Exception as exc:
                        await read_writer.send(exc)
                        continue
                    await read_writer.send(SessionMessage(message))
                if len(buffer) > byte_limit:
                    on_overflow()
                    await read_writer.send(McpOutputTooLarge())
                    return

    async def stdin_writer() -> None:
        assert process.stdin is not None
        async with write_reader:
            async for session_message in write_reader:
                payload = session_message.message.model_dump_json(
                    by_alias=True, exclude_none=True
                )
                await process.stdin.send((payload + "\n").encode(server.encoding))

    async with anyio.create_task_group() as tasks, process:
        tasks.start_soon(stdout_reader)
        tasks.start_soon(stdin_writer)
        try:
            yield read_stream, write_stream
        finally:
            if process.stdin is not None:
                try:
                    await process.stdin.aclose()
                except Exception:
                    pass
            try:
                with anyio.fail_after(PROCESS_TERMINATION_TIMEOUT):
                    await process.wait()
            except TimeoutError:
                await _terminate_process_tree(process)
            except ProcessLookupError:
                pass
            tasks.cancel_scope.cancel()
            await read_stream.aclose()
            await write_stream.aclose()
            await read_writer.aclose()
            await write_reader.aclose()


@asynccontextmanager
async def open_mcp_sdk_session(connection: McpConnection):
    with _suppress_transport_logs():
        async with AsyncExitStack() as stack:
            if connection.transport == "streamable_http":
                transport = _PinnedHttpTransport(
                    pinned_address=connection.pinned_address or "",
                    server_hostname=connection.server_hostname or "",
                    port=connection.port or 80,
                    byte_limit=connection.raw_output_byte_limit,
                    on_overflow=lambda: setattr(
                        connection, "output_limit_exceeded", True
                    ),
                )
                client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=connection.headers,
                        follow_redirects=False,
                        transport=transport,
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
                errlog = stack.enter_context(
                    open(os.devnull, "w", encoding="utf-8")
                )
                read_stream, write_stream = await stack.enter_async_context(
                    _bounded_stdio_client(
                        parameters,
                        byte_limit=connection.raw_output_byte_limit,
                        startup_timeout_seconds=connection.startup_timeout_seconds,
                        on_overflow=lambda: setattr(
                            connection, "output_limit_exceeded", True
                        ),
                        errlog=errlog,
                    )
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
        except Exception as exc:
            nested = _find_mcp_error(exc)
            if nested is not None:
                return self._record_test(server, "failed", nested.code)
            return self._record_test(server, "failed", "mcp.connection_failed")
        return self._record_test(server, "success", None)

    def discover_server(self, server_id: str) -> McpOperationOutcome:
        server = self._owned_server(server_id)
        try:
            tools, _ = self._run(server, self._discover_operation)
            self._replace_catalog(server, tools)
        except McpServiceError as exc:
            self.session.rollback()
            return self._record_test(server, "failed", exc.code)
        except Exception as exc:
            self.session.rollback()
            nested = _find_mcp_error(exc)
            if nested is not None:
                return self._record_test(server, "failed", nested.code)
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
        try:
            result, secret_values = self._run(
                server,
                lambda client: self._call_operation(client, tool.name, arguments),
            )
        except McpServiceError:
            raise
        except Exception as exc:
            nested = _find_mcp_error(exc)
            if nested is not None:
                raise nested
            raise McpServiceError("mcp.execution_failed") from None
        return self._normalize_result(result, secret_values=secret_values)

    def invoke_approved_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> McpInvocationResult:
        """Execute an already-durable, explicitly approved write operation."""
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
        try:
            result, secret_values = self._run(
                server,
                lambda client: self._call_operation(client, tool.name, arguments),
            )
        except McpServiceError:
            raise
        except Exception as exc:
            nested = _find_mcp_error(exc)
            if nested is not None:
                raise nested
            raise McpServiceError("mcp.execution_failed") from None
        return self._normalize_result(result, secret_values=secret_values)

    def confirm_stdio_trust(self, server_id: str) -> McpTrustOutcome:
        server = self._owned_server(server_id)
        if server.transport != "stdio" or not server.command:
            raise McpConfigurationError()
        server.trust_fingerprint = stdio_trust_fingerprint(
            server.command,
            list(server.args_json or []),
            server.working_directory,
        )
        server.trusted_at = datetime.now(timezone.utc)
        self.session.commit()
        return McpTrustOutcome(server.trust_fingerprint, server.trusted_at)

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

    def _connection(self, server: UserMcpServer) -> tuple[McpConnection, dict[str, str]]:
        if server.transport == "streamable_http":
            target = _resolve_http_target(server.url or "", self.resolver)
            secrets = self._secret_values(server.id)
            headers: dict[str, str] = {}
            for slot, value in secrets.items():
                if not slot.startswith("header:"):
                    continue
                name = slot.removeprefix("header:")
                if not _SAFE_HEADER_NAME.fullmatch(name) or "\r" in value or "\n" in value:
                    raise McpConfigurationError()
                headers[name] = value
            return (
                McpConnection(
                    server_id=server.id,
                    transport="streamable_http",
                    url=target.url,
                    headers=headers,
                    pinned_address=target.pinned_address,
                    server_hostname=target.hostname,
                    port=target.port,
                    raw_output_byte_limit=self.raw_output_byte_limit,
                    startup_timeout_seconds=self.startup_timeout_seconds,
                ),
                secrets,
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
        secrets = self._secret_values(server.id)
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
        return (
            McpConnection(
                server_id=server.id,
                transport="stdio",
                command=server.command,
                args=list(server.args_json or []),
                cwd=server.working_directory,
                env=env,
                raw_output_byte_limit=self.raw_output_byte_limit,
                startup_timeout_seconds=self.startup_timeout_seconds,
            ),
            secrets,
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
        connection, secrets = self._connection(server)

        async def execute():
            try:
                with anyio.fail_after(
                    self.startup_timeout_seconds + self.call_timeout_seconds
                ):
                    async with self.session_factory(connection) as client:
                        with anyio.fail_after(self.startup_timeout_seconds):
                            await client.initialize()
                        with anyio.fail_after(self.call_timeout_seconds):
                            return await operation(client)
            except TimeoutError:
                raise McpServiceError("mcp.timeout") from None

        try:
            try:
                value = anyio.run(execute)
            except BaseException:
                if connection.output_limit_exceeded:
                    raise McpOutputTooLarge() from None
                raise
            return value, tuple(secrets.values())
        finally:
            connection.headers.clear()
            connection.env.clear()
            secrets.clear()

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


def _find_mcp_error(exc: BaseException) -> McpServiceError | None:
    if isinstance(exc, McpServiceError):
        return exc
    for nested in getattr(exc, "exceptions", ()):
        found = _find_mcp_error(nested)
        if found is not None:
            return found
    return None


__all__ = [
    "McpApplicationService",
    "McpArgumentsInvalid",
    "McpConfigurationError",
    "McpConnection",
    "McpInvocationResult",
    "McpOperationOutcome",
    "McpResourceNotFound",
    "McpServiceError",
    "McpTrustOutcome",
    "McpTrustRequired",
    "ToolApprovalRequired",
    "open_mcp_sdk_session",
    "registry_tool_name",
    "stdio_trust_fingerprint",
    "validate_mcp_http_url",
]
