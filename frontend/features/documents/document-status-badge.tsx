import type { DocumentStatus } from "./types";
import { useLocale } from "@/components/providers/locale-provider";


const statusView: Record<DocumentStatus, { labelKey: string; className: string }> = {
  pending: { labelKey: "document.pending", className: "border-slate-200 bg-slate-50 text-slate-600" },
  processing: { labelKey: "document.parsing", className: "border-amber-200 bg-amber-50 text-amber-700" },
  success: { labelKey: "document.searchable", className: "border-teal/20 bg-tealSoft text-teal" },
  failed: { labelKey: "document.failed", className: "border-red-200 bg-red-50 text-red-700" },
};

export function DocumentStatusBadge({ status }: { status: DocumentStatus }) {
  const { t } = useLocale();
  const view = statusView[status];
  return (
    <span
      data-testid={`document-status-${status}`}
      className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${view.className}`}
    >
      {t(view.labelKey)}
    </span>
  );
}
