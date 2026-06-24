from __future__ import annotations

from backend.app.db import SessionLocal
from backend.app.services.official_sources import search_official_learning_sources as search_sources


def create_mcp_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "MCP SDK is not installed. Install project dependencies with `mcp[cli]>=1.27,<2`."
        ) from exc

    mcp = FastMCP("Adaptive Tutor Learning Sources", json_response=True)

    @mcp.tool()
    def search_official_learning_sources(query: str, domains: list[str]) -> list[dict]:
        """Search only whitelisted official learning sources and audit the call."""
        with SessionLocal() as session:
            return search_sources(session, query=query, domains=domains)

    return mcp


if __name__ == "__main__":
    create_mcp_server().run(transport="streamable-http")
