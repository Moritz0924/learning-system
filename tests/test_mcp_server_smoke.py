from __future__ import annotations

import asyncio

from backend.app.mcp_server import create_mcp_server


def test_mcp_server_starts_and_exposes_official_source_tool():
    server = create_mcp_server()

    tools = asyncio.run(server.list_tools())

    assert server.name == "Adaptive Tutor Learning Sources"
    assert [tool.name for tool in tools] == ["search_official_learning_sources"]
    assert tools[0].inputSchema["required"] == ["query", "domains"]
    assert tools[0].inputSchema["properties"]["domains"]["type"] == "array"
