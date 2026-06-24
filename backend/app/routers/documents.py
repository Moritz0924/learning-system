import base64
import binascii

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.db import get_session
from backend.app.services.stage3 import create_document_record, list_document_records


router = APIRouter(prefix="/api/documents", tags=["documents"])


class DocumentUploadRequest(BaseModel):
    user_id: str
    filename: str
    mime_type: str = "text/plain"
    content: str = ""
    content_base64: str | None = None
    source_url: str | None = None


@router.post("/upload", status_code=201)
def upload_document_endpoint(
    payload: DocumentUploadRequest,
    session: Session = Depends(get_session),
) -> dict:
    try:
        content_bytes = (
            base64.b64decode(payload.content_base64.encode("ascii"), validate=True)
            if payload.content_base64 is not None
            else payload.content.encode("utf-8")
        )
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise HTTPException(status_code=400, detail="content_base64 must be valid base64") from exc
    if not content_bytes:
        raise HTTPException(status_code=400, detail="document upload content is required")
    try:
        return create_document_record(
            session,
            user_id=payload.user_id,
            filename=payload.filename,
            mime_type=payload.mime_type,
            content=payload.content,
            content_bytes=content_bytes,
            source_url=payload.source_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_documents_endpoint(
    user_id: str = Query(...),
    session: Session = Depends(get_session),
) -> dict:
    return {"documents": list_document_records(session, user_id=user_id)}
