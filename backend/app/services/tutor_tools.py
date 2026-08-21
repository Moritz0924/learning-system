from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from adaptive_tutor.tutor.agent_contracts import ToolSpec
from adaptive_tutor.tutor.tool_router import (
    HandlerResult,
    RegisteredTool,
    ToolApprovalInterrupt,
    ToolRouter,
)
from backend.app.services.official_sources import (
    search_official_learning_sources,
    search_official_learning_sources_raw,
)
from backend.app.services.learning_sources import (
    record_learning_source_tool_call,
    search_learning_sources,
    search_learning_sources_raw,
)
from backend.app.services.tool_evidence import map_learning_source_search_evidence, map_official_search_evidence
from backend.app.application.mcp_service import (
    McpApplicationService,
    McpConfigurationError,
    McpInvocationResult,
    SessionFactory,
    registry_tool_name,
)
from backend.app.infrastructure.secrets import SecretStore
from backend.app.models import UserMcpServer, UserMcpTool


class OfficialSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=512)
    domains: list[str] = Field(min_length=1, max_length=5)


class LearningSourceSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=512)


def build_tutor_tool_router(
    session: Session | None = None,
    *,
    agent_run_id: str | None = None,
    user_id: str | None = None,
    secret_store: SecretStore | None = None,
    include_mcp: bool = False,
    mcp_session_factory: SessionFactory | None = None,
    mcp_resolver=None,
) -> ToolRouter:
    legacy_handler = None
    if session is not None:
        legacy_handler = lambda arguments: search_official_learning_sources(
            session,
            query=str(arguments.get("query", "")),
            domains=list(arguments.get("domains", [])),
        )
    learning_sources_legacy_handler = None
    learning_sources_completion_handler = None
    if session is not None:
        learning_sources_legacy_handler = lambda arguments: search_learning_sources(
            session, query=str(arguments.get("query", ""))
        )
        learning_sources_completion_handler = lambda arguments, value, status: record_learning_source_tool_call(
            session,
            agent_run_id=agent_run_id,
            query=str(arguments.get("query", "")),
            results=value if status == "success" and isinstance(value, list) else [],
            status=status,
        )
    registry = {
        "search_official_learning_sources": RegisteredTool(
                spec=ToolSpec(
                    name="search_official_learning_sources",
                    description=(
                        "Search whitelisted official technical documentation when local "
                        "learning materials are insufficient."
                    ),
                    input_schema=OfficialSearchArguments.model_json_schema(),
                    safety_class="read_only",
                    agent_visible=True,
                ),
                handler=_search_official_tool,
                argument_model=OfficialSearchArguments,
                legacy_handler=legacy_handler,
                evidence_mapper=map_official_search_evidence,
            ),
        "search_learning_sources": RegisteredTool(
            spec=ToolSpec(
                name="search_learning_sources",
                description="Search public learning sources when local material is insufficient.",
                input_schema=LearningSourceSearchArguments.model_json_schema(),
                safety_class="read_only",
                agent_visible=True,
            ),
            handler=_search_learning_sources_tool,
            argument_model=LearningSourceSearchArguments,
            legacy_handler=learning_sources_legacy_handler,
            evidence_mapper=map_learning_source_search_evidence,
            completion_handler=learning_sources_completion_handler,
        ),
    }
    if include_mcp and session is not None and user_id is not None:
        service_kwargs = {}
        if mcp_session_factory is not None:
            service_kwargs["session_factory"] = mcp_session_factory
        if mcp_resolver is not None:
            service_kwargs["resolver"] = mcp_resolver
        mcp_service = McpApplicationService(
            session,
            user_id=user_id,
            secret_store=secret_store,
            **service_kwargs,
        )
        rows = session.execute(
            select(UserMcpServer, UserMcpTool)
            .join(UserMcpTool, UserMcpTool.mcp_server_id == UserMcpServer.id)
            .where(
                UserMcpServer.user_id == user_id,
                UserMcpServer.enabled.is_(True),
                UserMcpTool.enabled.is_(True),
            )
            .order_by(UserMcpServer.id, UserMcpTool.name)
        ).all()
        for server, tool in rows:
            name = registry_tool_name(server.id, tool.name)
            if name in registry:
                raise McpConfigurationError()
            read_only = tool.annotations_json.get("readOnlyHint") is True
            registry[name] = RegisteredTool(
                spec=ToolSpec(
                    name=name,
                    description=(tool.description or f"MCP tool {tool.name}")[:1000],
                    input_schema=tool.input_schema_json,
                    safety_class="read_only" if read_only else "proposal_only",
                    agent_visible=True,
                ),
                handler=lambda arguments, server_id=server.id, tool_name=tool.name: _invoke_mcp_tool(
                    mcp_service, server_id, tool_name, arguments
                ),
            )
    return ToolRouter(registry, allow_agent_proposals=include_mcp)


def _invoke_mcp_tool(
    service: McpApplicationService,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    result = service.invoke_tool(server_id, tool_name, arguments)
    return (
        HandlerResult(result.value, result.truncated)
        if isinstance(result, McpInvocationResult)
        else _raise_approval_interrupt(result, arguments)
    )


def _raise_approval_interrupt(result: Any, arguments: dict[str, Any]) -> Any:
    from backend.app.application.mcp_service import ToolApprovalRequired

    if isinstance(result, ToolApprovalRequired):
        raise ToolApprovalInterrupt(
            server_id=result.server_id,
            tool_name=result.tool_name,
            arguments=arguments,
        )
    return result


def _search_official_tool(arguments: dict[str, Any]) -> list[dict]:
    return search_official_learning_sources_raw(
        query=arguments["query"],
        domains=arguments["domains"],
    )


def _search_learning_sources_tool(arguments: dict[str, Any]) -> list[dict]:
    return search_learning_sources_raw(query=arguments["query"])
