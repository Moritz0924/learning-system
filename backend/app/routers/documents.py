import base64
import binascii

from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.api.schemas.documents import DocumentListResponse, DocumentStatusResponse
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.application.document_service import create_document_record, get_document_record, list_document_records
from backend.app.application.upload_reader import document_max_upload_bytes, read_upload_limited
from backend.app.core.exceptions import DocumentProcessingUnavailable, DocumentUploadTooLarge
from backend.app.services.document_parsing.exceptions import (
    CorruptedDocumentError,
    DocumentTooLargeError,
    FileTypeMismatchError,
    UnsupportedDocumentTypeError,
)
from backend.app.services.document_parsing.file_validation import validate_upload_document


router = APIRouter(prefix="/api/documents", tags=["documents"])


class DocumentUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str
    mime_type: str = "text/plain"
    content: str = ""
    content_base64: str | None = None
    source_url: str | None = None


@router.post(
    "/upload",
    status_code=201,
    deprecated=True,
    response_model=DocumentStatusResponse,
)
def upload_document_endpoint(
    payload: DocumentUploadRequest,
    principal: Principal = Depends(get_current_principal),
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
            user_id=principal.user_id,
            filename=payload.filename,
            mime_type=payload.mime_type,
            content=payload.content,
            content_bytes=content_bytes,
            source_url=payload.source_url,
        )
    except DocumentUploadTooLarge as exc:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DocumentProcessingUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post("", status_code=201, response_model=DocumentStatusResponse)
async def upload_multipart_document_endpoint(
    request: Request,
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    form = await request.form()
    form_items = form.multi_items()
    if len(form_items) != 1 or form_items[0][0] != "file":
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="multipart upload accepts only the file field",
        )
    try:
        content_bytes = await read_upload_limited(
            file,
            max_bytes=document_max_upload_bytes(),
        )
        validated = validate_upload_document(
            content=content_bytes,
            filename=file.filename,
            mime_type=file.content_type,
        )
        return create_document_record(
            session,
            user_id=principal.user_id,
            filename=validated.filename,
            mime_type=validated.mime_type,
            content_bytes=content_bytes,
        )
    except (DocumentUploadTooLarge, DocumentTooLargeError) as exc:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc
    except (FileTypeMismatchError, UnsupportedDocumentTypeError) as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except CorruptedDocumentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except DocumentProcessingUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("", response_model=DocumentListResponse)
def list_documents_endpoint(
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    return {"documents": list_document_records(session, user_id=principal.user_id)}


@router.get("/{document_id}", response_model=DocumentStatusResponse)
def get_document_endpoint(
    document_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    document = get_document_record(session, user_id=principal.user_id, document_id=document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document
