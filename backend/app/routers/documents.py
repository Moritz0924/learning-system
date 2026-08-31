import base64
import binascii

from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.api.schemas.documents import DocumentListResponse, DocumentStatusResponse
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.application.document_service import (
    assign_document_goal,
    create_document_record,
    get_document_record,
    list_document_records,
)
from backend.app.application.upload_reader import document_max_upload_bytes, read_upload_limited
from backend.app.core.exceptions import DocumentProcessingUnavailable, DocumentUploadTooLarge
from backend.app.services.document_parsing.exceptions import (
    CorruptedDocumentError,
    DocumentTooLargeError,
    FileTypeMismatchError,
    UnsupportedDocumentTypeError,
)
from backend.app.services.document_parsing.file_validation import validate_upload_document
from backend.app.infrastructure.secrets import SecretStore
from backend.app.routers.config import get_secret_store


router = APIRouter(prefix="/api/documents", tags=["documents"])


class DocumentUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_id: str
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
    secret_store: SecretStore | None = Depends(get_secret_store),
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
            goal_id=payload.goal_id,
            filename=payload.filename,
            mime_type=payload.mime_type,
            content=payload.content,
            content_bytes=content_bytes,
            source_url=payload.source_url,
            secret_store=secret_store,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document goal not found") from exc
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
    goal_id: str = Form(..., min_length=1),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
    secret_store: SecretStore | None = Depends(get_secret_store),
) -> dict:
    form = await request.form()
    form_items = form.multi_items()
    if len(form_items) != 2 or sorted(item[0] for item in form_items) != ["file", "goal_id"]:
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="multipart upload accepts only the file and goal_id fields",
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
            goal_id=goal_id,
            filename=validated.filename,
            mime_type=validated.mime_type,
            content_bytes=content_bytes,
            secret_store=secret_store,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document goal not found") from exc
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
    goal_id: str | None = Query(default=None, min_length=1),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    return {
        "documents": list_document_records(
            session,
            user_id=principal.user_id,
            goal_id=goal_id,
        )
    }


class DocumentGoalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_id: str


@router.put("/{document_id}/goal", response_model=DocumentStatusResponse)
def assign_document_goal_endpoint(
    document_id: str,
    payload: DocumentGoalRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    document = assign_document_goal(
        session,
        user_id=principal.user_id,
        document_id=document_id,
        goal_id=payload.goal_id,
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


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
