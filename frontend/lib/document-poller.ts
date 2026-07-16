export type DocumentPollStatus = "pending" | "processing" | "success" | "failed";

export type PolledDocument = {
  id: string;
  parse_status: DocumentPollStatus | string;
  parse_error?: string | null;
};

export function pollDocument(
  documentId: string,
  load: (documentId: string) => Promise<PolledDocument>,
  onUpdate: (document: PolledDocument) => void,
  onTimeout: () => void,
): () => void {
  let cancelled = false;
  let failures = 0;
  const startedAt = Date.now();
  let interval = 1_000;

  const run = async () => {
    while (!cancelled) {
      await new Promise<void>((resolve) => window.setTimeout(resolve, interval));
      if (cancelled) return;
      if (Date.now() - startedAt >= 90_000) {
        onTimeout();
        return;
      }
      try {
        const document = await load(documentId);
        failures = 0;
        onUpdate(document);
        if (document.parse_status === "success" || document.parse_status === "failed") return;
        interval = Math.min(5_000, Math.max(2_000, interval));
      } catch {
        failures += 1;
        if (failures >= 3) {
          onTimeout();
          return;
        }
        interval = Math.min(5_000, Math.max(2_000, interval));
      }
    }
  };
  void run();
  return () => { cancelled = true; };
}
