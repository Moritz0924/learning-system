export type DocumentStatus = "pending" | "processing" | "success" | "failed";

export type DocumentRecord = {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number | null;
  parse_status: DocumentStatus;
  parse_error_code: string | null;
  parse_error: string | null;
  page_count: number | null;
  block_count: number | null;
  parser_version: string | null;
  created_at: string;
  processing_started_at: string | null;
  processing_completed_at: string | null;
};

export type DocumentListResponse = {
  documents: DocumentRecord[];
};
