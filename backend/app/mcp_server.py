from __future__ import annotations

import asyncio
import base64
import binascii
import os
from hashlib import sha256

from backend.app.db import SessionLocal
from backend.app.models import ToolCall
from backend.app.services.document_parsing.exceptions import DocumentParsingError
from backend.app.services.document_parsing.parser import DocumentParser
from backend.app.services.official_sources import search_official_learning_sources as search_sources


def create_mcp_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "MCP SDK is not installed. Install project dependencies with `mcp[cli]>=1.27,<2`."
        ) from exc

    mcp = FastMCP(
        "Adaptive Tutor Learning Sources",
        host=os.getenv("MCP_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_int_env("MCP_PORT", 8001),
        json_response=True,
    )

    @mcp.tool()
    def search_official_learning_sources(query: str, domains: list[str]) -> list[dict]:
        """Search only whitelisted official learning sources and audit the call."""
        with SessionLocal() as session:
            return search_sources(session, query=query, domains=domains)

    @mcp.tool()
    async def ocr_image(content_base64: str, filename: str, mime_type: str) -> dict:
        """Extract structured OCR text from a single image without writing to RAG."""
        return await _parse_mcp_document(content_base64, filename, mime_type, image_only=True)

    @mcp.tool()
    async def parse_document(content_base64: str, filename: str, mime_type: str) -> dict:
        """Parse an image, PDF, or PPTX without writing to the knowledge base."""
        return await _parse_mcp_document(content_base64, filename, mime_type, image_only=False)

    return mcp


if __name__ == "__main__":
    create_mcp_server().run(transport="streamable-http")


async def _parse_mcp_document(content_base64: str, filename: str, mime_type: str, *, image_only: bool) -> dict:
    max_chars = _int_env("DOCUMENT_MCP_MAX_RESPONSE_CHARS", 120_000) * 2
    if len(content_base64) > max_chars:
        return _mcp_error("document_too_large")
    try:
        content = base64.b64decode(content_base64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        return _mcp_error("invalid_base64")
    content_hash = sha256(content).hexdigest()
    tool_name = "ocr_image" if image_only else "parse_document"
    try:
        parser = DocumentParser()
        result = await (parser.parse_image(content=content, filename=filename, mime_type=mime_type) if image_only else parser.parse_document(content=content, filename=filename, mime_type=mime_type))
        if image_only and result.file_type.value != "image":
            return _mcp_error("unsupported_document_type")
        payload = result.model_dump(mode="json")
        _truncate_payload(payload)
        _record_document_tool_call(tool_name, content_hash, filename, payload)
        return payload
    except (DocumentParsingError, ValueError) as exc:
        error_code = getattr(exc, "error_code", "document_parsing_error")
        _record_document_tool_call(tool_name, content_hash, filename, {"status": "failed", "error_code": error_code})
        return _mcp_error(error_code)


def _truncate_payload(payload: dict) -> None:
    max_chars = _int_env("DOCUMENT_MCP_MAX_RESPONSE_CHARS", 120_000)
    remaining = max_chars
    for block in payload.get("blocks", []):
        text = block.get("text", "")
        if len(text) <= remaining:
            remaining -= len(text)
            continue
        block["text"] = text[: max(0, remaining)]
        payload["truncated"] = True
        remaining = 0


def _record_document_tool_call(tool_name: str, content_hash: str, filename: str, payload: dict) -> None:
    with SessionLocal() as session:
        session.add(ToolCall(
            id=f"tool-{__import__('uuid').uuid4()}", agent_run_id=None, tool_name=tool_name,
            request_hash=sha256(f"{tool_name}|{content_hash}".encode()).hexdigest(), source_urls=[],
            status="success" if payload.get("status") != "failed" else "failed",
            response_summary={
                "content_sha256": content_hash,
                "filename": filename,
                "parse_status": payload.get("status"),
                "page_count": payload.get("page_count", 0),
                "block_count": payload.get("block_count", 0),
                "truncated": bool(payload.get("truncated")),
                "error_code": payload.get("error_code"),
            },
        ))
        session.commit()


def _mcp_error(error_code: str) -> dict:
    return {"status": "failed", "error_code": error_code}


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
