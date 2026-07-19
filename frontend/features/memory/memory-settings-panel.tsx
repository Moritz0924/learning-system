"use client";

import { useCallback, useEffect, useState } from "react";

import {
  disableMemory,
  getMemoryPrivacy,
  listMemories,
  updateMemoryPrivacy,
} from "./memory-api";
import { defaultMemoryPrivacy } from "./types";
import type {
  MemoryOrigin,
  MemoryPrivacySettings,
  MemoryRecordPublic,
  MemoryType,
} from "./types";


const PAGE_SIZE = 10;
const memoryTypes: MemoryType[] = [
  "learning_preference",
  "long_term_goal",
  "mastery_summary",
  "learning_milestone",
];

export function MemorySettingsPanel({ goalId }: { goalId?: string }) {
  const [privacy, setPrivacy] = useState<MemoryPrivacySettings>(defaultMemoryPrivacy);
  const [items, setItems] = useState<MemoryRecordPublic[]>([]);
  const [memoryType, setMemoryType] = useState<MemoryType | "">("");
  const [sourceCategory, setSourceCategory] = useState<MemoryOrigin | "">("");
  const [status, setStatus] = useState<"active" | "inactive" | "all">("active");
  const [offset, setOffset] = useState(0);
  const [returnedCount, setReturnedCount] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(
    () => Promise.all([
      getMemoryPrivacy(),
      listMemories({
        goalId,
        memoryType,
        sourceCategory,
        status,
        includeUserScope: true,
        limit: PAGE_SIZE,
        offset,
      }),
    ]),
    [goalId, memoryType, offset, sourceCategory, status],
  );

  const refresh = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const [nextPrivacy, result] = await load();
      setPrivacy(nextPrivacy);
      setItems(result.items);
      setReturnedCount(result.returned_count);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load memory settings.");
    } finally {
      setBusy(false);
    }
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    void load()
      .then(([nextPrivacy, result]) => {
        if (cancelled) return;
        setPrivacy(nextPrivacy);
        setItems(result.items);
        setReturnedCount(result.returned_count);
        setError("");
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Unable to load memory settings.");
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => { cancelled = true; };
  }, [load]);

  const changePrivacy = async (key: keyof MemoryPrivacySettings, checked: boolean) => {
    const next = { ...privacy, [key]: checked };
    const previous = privacy;
    setPrivacy(next);
    setBusy(true);
    setError("");
    try {
      setPrivacy(await updateMemoryPrivacy(next));
    } catch (cause) {
      setPrivacy(previous);
      setError(cause instanceof Error ? cause.message : "Unable to update memory privacy.");
    } finally {
      setBusy(false);
    }
  };

  const disable = async (memoryId: string) => {
    setBusy(true);
    setError("");
    try {
      await disableMemory(memoryId);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to disable memory.");
      setBusy(false);
    }
  };

  return (
    <section data-testid="memory-settings-panel" className="mb-4 rounded-lg border border-line bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold text-teal">Long-term memory</p>
          <h2 className="mt-1 font-semibold">Privacy and saved memory</h2>
          <p className="mt-2 text-xs text-muted">
            Supported types: {memoryTypes.join(", ")}. Disabling memory never deletes stored records.
          </p>
        </div>
        <button className="h-9 rounded-lg border border-line px-3 text-xs text-teal" onClick={() => void refresh()} disabled={busy} type="button">
          Refresh
        </button>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {([
          ["enabled", "Enable long-term memory"],
          ["allow_explicit_user", "Explicit user statements"],
          ["allow_system_inference", "System inferences"],
          ["allow_learning_results", "Learning results"],
        ] as const).map(([key, label]) => (
          <label key={key} className="flex items-center gap-2 rounded-lg border border-line p-3 text-sm">
            <input
              data-testid={`memory-privacy-${key}`}
              type="checkbox"
              checked={privacy[key]}
              disabled={busy}
              onChange={(event) => void changePrivacy(key, event.target.checked)}
            />
            {label}
          </label>
        ))}
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-3">
        <select data-testid="memory-type-filter" className="h-10 rounded-lg border border-line px-3 text-sm" value={memoryType} onChange={(event) => { setOffset(0); setMemoryType(event.target.value as MemoryType | ""); }}>
          <option value="">All types</option>
          {memoryTypes.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        <select data-testid="memory-source-filter" className="h-10 rounded-lg border border-line px-3 text-sm" value={sourceCategory} onChange={(event) => { setOffset(0); setSourceCategory(event.target.value as MemoryOrigin | ""); }}>
          <option value="">All sources</option>
          <option value="explicit_user_statement">Explicit user statement</option>
          <option value="system_inference">System inference</option>
          <option value="learning_result">Learning result</option>
        </select>
        <select data-testid="memory-status-filter" className="h-10 rounded-lg border border-line px-3 text-sm" value={status} onChange={(event) => { setOffset(0); setStatus(event.target.value as "active" | "inactive" | "all"); }}>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
          <option value="all">All</option>
        </select>
      </div>

      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
      <div className="mt-4 space-y-3">
        {items.map((memory) => (
          <article data-testid="memory-row" key={memory.memory_id} className="rounded-lg border border-line bg-[#fbfdfc] p-4 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="font-semibold">{memory.memory_type}</div>
                <div className="mt-1 text-xs text-muted">{memory.origin} · {memory.scope} · confidence {memory.confidence}</div>
              </div>
              {memory.is_enabled && (
                <button data-testid="disable-memory" className="h-9 rounded-lg border border-red-200 px-3 text-xs text-red-700" onClick={() => void disable(memory.memory_id)} disabled={busy} type="button">
                  Disable
                </button>
              )}
            </div>
            <pre className="mt-3 overflow-auto whitespace-pre-wrap text-xs text-muted">{JSON.stringify(memory.content, null, 2)}</pre>
          </article>
        ))}
        {!busy && items.length === 0 && <p className="rounded-lg border border-dashed border-line p-4 text-sm text-muted">No memories match these filters.</p>}
      </div>
      <div className="mt-4 flex items-center justify-between text-xs">
        <button className="rounded-lg border border-line px-3 py-2 disabled:opacity-40" disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} type="button">Previous</button>
        <span className="text-muted">Offset {offset}</span>
        <button className="rounded-lg border border-line px-3 py-2 disabled:opacity-40" disabled={busy || returnedCount < PAGE_SIZE} onClick={() => setOffset(offset + PAGE_SIZE)} type="button">Next</button>
      </div>
    </section>
  );
}
