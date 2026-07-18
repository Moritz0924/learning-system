from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_api_preserves_browser_generated_multipart_content_type():
    api = (ROOT / "frontend/lib/api.ts").read_text(encoding="utf-8")
    document_api = (ROOT / "frontend/features/documents/document-api.ts").read_text(
        encoding="utf-8"
    )

    assert "init.body instanceof FormData" in api
    assert '!isFormData && !headers.has("Content-Type")' in api
    assert "const body = new FormData()" in document_api
    assert 'body.append("file", file)' in document_api
    assert 'apiRequest<DocumentRecord>("/api/documents"' in document_api
    assert 'headers: { "Content-Type": "multipart/form-data" }' not in document_api


def test_document_types_match_safe_status_response_without_internal_fields():
    types = (ROOT / "frontend/features/documents/types.ts").read_text(encoding="utf-8")

    for field in (
        "size_bytes",
        "parse_error_code",
        "page_count",
        "block_count",
        "parser_version",
        "processing_started_at",
        "processing_completed_at",
    ):
        assert field in types
    for internal_field in ("object_key", "trusted_level", "owner_user_id", "chunks"):
        assert internal_field not in types


def test_file_upload_and_markdown_note_are_separate_provider_actions():
    provider = (ROOT / "frontend/components/learning-provider.tsx").read_text(encoding="utf-8")

    assert "uploadFile: (file: File) => Promise<boolean>" in provider
    assert "saveNote: () => Promise<void>" in provider
    assert 'runBusy("fileUpload"' in provider
    assert 'runBusy("document"' in provider
    assert "uploadDocumentFile(file)" in provider
    assert "saveMarkdownNote(content)" in provider
    assert "identityEpochRef" in provider
    assert "documentPollersRef.current.has(documentId)" in provider
    assert "documentPollersRef.current.values()" in provider


def test_upload_ui_supports_validation_cancel_and_safe_statuses():
    upload = (ROOT / "frontend/features/documents/document-upload-panel.tsx").read_text(
        encoding="utf-8"
    )
    listing = (ROOT / "frontend/features/documents/document-list.tsx").read_text(
        encoding="utf-8"
    )
    badge = (ROOT / "frontend/features/documents/document-status-badge.tsx").read_text(
        encoding="utf-8"
    )

    for test_id in (
        "document-drop-zone",
        "document-file-input",
        "selected-document-name",
        "cancel-document-selection",
        "upload-selected-document",
        "document-validation-error",
    ):
        assert test_id in upload
    assert "validateDocumentFile" in upload
    assert "current === submittedFile" in upload
    assert 'data-testid="document-row"' in listing
    assert 'data-testid="refresh-document-status"' in listing
    for status in ("pending", "processing", "success", "failed"):
        assert status in badge


def test_e2e_suite_serializes_access_to_its_shared_sqlite_backend():
    config = (ROOT / "frontend/playwright.config.ts").read_text(encoding="utf-8")

    assert "workers: 1" in config
