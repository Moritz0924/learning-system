import base64
import binascii

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.auth import get_current_user_id, validate_legacy_user_id
from backend.app.db import get_session
from backend.app.services.stage3 import DocumentProcessingUnavailable, create_document_record, list_document_records


router = APIRouter(prefix="/api/documents", tags=["documents"])


class DocumentUploadRequest(BaseModel):
    user_id: str | None = None
    filename: str
    mime_type: str = "text/plain"
    content: str = ""
    content_base64: str | None = None
    source_url: str | None = None


@router.post("/upload", status_code=201)
def upload_document_endpoint(
    payload: DocumentUploadRequest,
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    validate_legacy_user_id(payload.user_id, user_id)
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
            user_id=user_id,
            filename=payload.filename,
            mime_type=payload.mime_type,
            content=payload.content,
            content_bytes=content_bytes,
            source_url=payload.source_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DocumentProcessingUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("")
def list_documents_endpoint(
    legacy_user_id: str | None = Query(default=None, alias="user_id"),
    user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    validate_legacy_user_id(legacy_user_id, user_id)
    return {"documents": list_document_records(session, user_id=user_id)}
