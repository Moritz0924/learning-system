from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DocumentStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    filename: str
    mime_type: str
    size_bytes: int | None
    parse_status: Literal["pending", "processing", "success", "failed"]
    parse_error_code: str | None
    parse_error: str | None
    page_count: int | None
    block_count: int | None
    parser_version: str | None
    created_at: datetime
    processing_started_at: datetime | None
    processing_completed_at: datetime | None


class DocumentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[DocumentStatusResponse]
