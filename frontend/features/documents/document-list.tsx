import { MdRefresh } from "react-icons/md";

import { DocumentError } from "./document-error";
import { DocumentStatusBadge } from "./document-status-badge";
import type { DocumentRecord } from "./types";


function formatCreatedAt(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function formatSize(size: number | null) {
  if (size === null) return "大小未知";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function DocumentList({
  documents,
  onRefreshDocument,
}: {
  documents: DocumentRecord[];
  onRefreshDocument: (documentId: string) => Promise<void>;
}) {
  if (documents.length === 0) {
    return <div className="rounded-lg border border-dashed border-line p-5 text-sm text-muted">暂无上传资料。</div>;
  }

  return (
    <div className="space-y-3">
      {documents.map((document) => (
        <article data-testid="document-row" key={document.id} className="rounded-lg border border-line bg-white p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="truncate text-sm font-semibold">{document.filename}</h3>
              <p className="mt-1 text-xs text-muted">
                {formatSize(document.size_bytes)} · {formatCreatedAt(document.created_at)}
              </p>
            </div>
            <DocumentStatusBadge status={document.parse_status} />
          </div>

          {document.parse_status === "success" && (
            <p className="mt-3 text-xs text-muted">
              {document.page_count ?? 0} 页 · {document.block_count ?? 0} 块
              {document.parser_version ? ` · ${document.parser_version}` : ""}
            </p>
          )}
          {document.parse_status === "failed" && (
            <div className="mt-3 space-y-2">
              <DocumentError message={document.parse_error || "文件处理失败，请检查文件后重试。"} />
              {document.parse_error_code && <p className="text-xs text-muted">错误码：{document.parse_error_code}</p>}
            </div>
          )}
          {document.parse_status !== "success" && (
            <button
              data-testid="refresh-document-status"
              className="mt-3 flex h-8 items-center gap-1 rounded-lg border border-line px-2.5 text-xs font-semibold text-teal"
              onClick={() => void onRefreshDocument(document.id)}
              type="button"
            >
              <MdRefresh aria-hidden /> 刷新状态
            </button>
          )}
        </article>
      ))}
    </div>
  );
}
