from __future__ import annotations

from io import BytesIO

from pypdf import PdfWriter
from sqlalchemy import select

from backend.app.models import Document, DocumentChunk
from backend.app.services.stage3 import (
    DeterministicEmbeddingClient,
    SQLAlchemyRagRepository,
    create_document_record,
    process_document_upload,
)


def _simple_pdf_bytes(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    pdf = b"%PDF-1.4\n"
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += (
        b"trailer\n"
        + f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
        + b"startxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    return pdf


def _blank_pdf_bytes() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def test_markdown_upload_registers_pending_then_worker_makes_chunks_searchable(db_session):
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="rag-notes.md",
        mime_type="text/markdown",
        content="# RAG\nWorker chunks become searchable citations.",
        processing_mode="defer",
    )

    assert document["parse_status"] == "pending"

    result = process_document_upload(
        db_session,
        document_id=document["id"],
        content_bytes=b"# RAG\nWorker chunks become searchable citations.",
    )

    assert result == {"document_id": document["id"], "status": "success", "chunk_count": 1}
    stored = db_session.get(Document, document["id"])
    assert stored.parse_status == "success"
    chunks = db_session.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == document["id"])
    ).all()
    assert chunks[0].metadata_json == {
        "source_type": "markdown",
        "untrusted_input": True,
        "chunk_index": 1,
    }

    repository = SQLAlchemyRagRepository(db_session, DeterministicEmbeddingClient())
    retrieved = repository.retrieve("searchable citations", user_id="user-1", top_k=1)
    assert retrieved[0].document_id == document["id"]
    assert retrieved[0].source_title == "rag-notes.md"


def test_pdf_upload_extracts_page_text_and_records_page_metadata(db_session):
    pdf_bytes = _simple_pdf_bytes("PDF RAG retrieval note")
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="rag-guide.pdf",
        mime_type="application/pdf",
        content_bytes=pdf_bytes,
        processing_mode="defer",
    )

    result = process_document_upload(db_session, document_id=document["id"], content_bytes=pdf_bytes)

    assert result["status"] == "success"
    chunk = db_session.scalar(select(DocumentChunk).where(DocumentChunk.document_id == document["id"]))
    assert "PDF RAG retrieval note" in chunk.content
    assert chunk.metadata_json["source_type"] == "pdf"
    assert chunk.metadata_json["page_number"] == 1
    assert chunk.citation_label == "rag-guide.pdf page 1 chunk 1"


def test_worker_marks_unsupported_upload_failed_without_chunks(db_session):
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="spreadsheet.xls",
        mime_type="application/vnd.ms-excel",
        content_bytes=b"not text",
        processing_mode="defer",
    )

    result = process_document_upload(db_session, document_id=document["id"], content_bytes=b"not text")

    assert result == {"document_id": document["id"], "status": "failed", "chunk_count": 0}
    stored = db_session.get(Document, document["id"])
    assert stored.parse_status == "failed"
    chunk_count = db_session.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == document["id"])
    ).all()
    assert chunk_count == []


def test_worker_marks_pdf_without_extractable_text_failed_without_chunks(db_session):
    pdf_bytes = _blank_pdf_bytes()
    document = create_document_record(
        db_session,
        user_id="user-1",
        filename="blank.pdf",
        mime_type="application/pdf",
        content_bytes=pdf_bytes,
        processing_mode="defer",
    )

    result = process_document_upload(db_session, document_id=document["id"], content_bytes=pdf_bytes)

    assert result == {"document_id": document["id"], "status": "failed", "chunk_count": 0}
    stored = db_session.get(Document, document["id"])
    assert stored.parse_status == "failed"
    chunks = db_session.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == document["id"])
    ).all()
    assert chunks == []


def test_upload_endpoint_rejects_empty_content_and_invalid_base64(client):
    empty_response = client.post(
        "/api/documents/upload",
        json={"user_id": "user-1", "filename": "empty.md", "mime_type": "text/markdown", "content": ""},
    )
    invalid_response = client.post(
        "/api/documents/upload",
        json={
            "user_id": "user-1",
            "filename": "bad.pdf",
            "mime_type": "application/pdf",
            "content_base64": "not valid base64",
        },
    )

    assert empty_response.status_code == 400
    assert empty_response.json()["detail"] == "document upload content is required"
    assert invalid_response.status_code == 400
    assert invalid_response.json()["detail"] == "content_base64 must be valid base64"
