import { apiRequest, getRequest, postRequest } from "@/lib/api";

import type { DocumentListResponse, DocumentRecord } from "./types";


export function listDocuments() {
  return getRequest<DocumentListResponse>("/api/documents");
}

export function getDocument(documentId: string) {
  return getRequest<DocumentRecord>(`/api/documents/${encodeURIComponent(documentId)}`);
}

export function uploadDocumentFile(file: File, goalId: string) {
  const body = new FormData();
  body.append("goal_id", goalId);
  body.append("file", file);
  return apiRequest<DocumentRecord>("/api/documents", { method: "POST", body });
}

export function saveMarkdownNote(content: string, goalId: string) {
  return postRequest<DocumentRecord>("/api/documents/upload", {
    goal_id: goalId,
    filename: `learning-note-${Date.now()}.md`,
    mime_type: "text/markdown",
    content,
  });
}
