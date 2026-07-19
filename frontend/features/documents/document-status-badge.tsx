import type { DocumentStatus } from "./types";


const statusView: Record<DocumentStatus, { label: string; className: string }> = {
  pending: { label: "等待处理", className: "border-slate-200 bg-slate-50 text-slate-600" },
  processing: { label: "正在解析", className: "border-amber-200 bg-amber-50 text-amber-700" },
  success: { label: "可检索", className: "border-teal/20 bg-tealSoft text-teal" },
  failed: { label: "处理失败", className: "border-red-200 bg-red-50 text-red-700" },
};

export function DocumentStatusBadge({ status }: { status: DocumentStatus }) {
  const view = statusView[status];
  return (
    <span
      data-testid={`document-status-${status}`}
      className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${view.className}`}
    >
      {view.label}
    </span>
  );
}
