from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from adaptive_tutor.tutor.agent_contracts import ToolSpec
from adaptive_tutor.tutor.tool_router import RegisteredTool, ToolRouter
from backend.app.services.official_sources import (
    search_official_learning_sources,
    search_official_learning_sources_raw,
)
from backend.app.services.tool_evidence import map_official_search_evidence


class OfficialSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=512)
    domains: list[str] = Field(min_length=1, max_length=5)


def build_tutor_tool_router(session: Session | None = None) -> ToolRouter:
    legacy_handler = None
    if session is not None:
        legacy_handler = lambda arguments: search_official_learning_sources(
            session,
            query=str(arguments.get("query", "")),
            domains=list(arguments.get("domains", [])),
        )
    return ToolRouter(
        {
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
            )
        }
    )


def _search_official_tool(arguments: dict[str, Any]) -> list[dict]:
    return search_official_learning_sources_raw(
        query=arguments["query"],
        domains=arguments["domains"],
    )
