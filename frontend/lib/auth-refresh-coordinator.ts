import { ApiError } from "./api";

const CHANNEL_NAME = "learning-system-auth-refresh";
const LOCK_NAME = "learning-system-refresh";
const WAIT_TIMEOUT_MS = 4_000;

type RefreshSignal = "refresh-started" | "refresh-succeeded" | "refresh-failed";

function isRefreshRace(error: unknown): boolean {
  return error instanceof ApiError && error.code === "auth.refresh_race";
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function refreshWithSignals<T>(refresh: () => Promise<T>): Promise<T> {
  const channel = typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel(CHANNEL_NAME);
  let peerCompleted: (() => void) | null = null;
  const peerSuccess = new Promise<void>((resolve) => { peerCompleted = resolve; });
  channel?.addEventListener("message", (event) => {
    if (event.data?.type === "refresh-succeeded") peerCompleted?.();
  });
  channel?.postMessage({ type: "refresh-started" satisfies RefreshSignal });
  try {
    const result = await refresh();
    channel?.postMessage({ type: "refresh-succeeded" satisfies RefreshSignal });
    return result;
  } catch (error) {
    if (!isRefreshRace(error)) {
      channel?.postMessage({ type: "refresh-failed" satisfies RefreshSignal });
      throw error;
    }
    await Promise.race([peerSuccess, delay(WAIT_TIMEOUT_MS)]);
    const result = await refresh();
    channel?.postMessage({ type: "refresh-succeeded" satisfies RefreshSignal });
    return result;
  } finally {
    channel?.close();
  }
}

export async function coordinateRefresh<T>(refresh: () => Promise<T>): Promise<T> {
  if (typeof navigator !== "undefined" && navigator.locks) {
    return navigator.locks.request(LOCK_NAME, { mode: "exclusive" }, () => refreshWithSignals(refresh));
  }
  return refreshWithSignals(refresh);
}
