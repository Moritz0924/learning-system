const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

let accessToken: string | null = null;
let refreshHandler: (() => Promise<string | null>) | null = null;

export function setAccessToken(value: string | null) { accessToken = value; }
export function setRefreshHandler(value: (() => Promise<string | null>) | null) { refreshHandler = value; }

export async function apiRequest<T>(path: string, init: RequestInit = {}, retried = false): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (accessToken && !path.startsWith("/api/auth/")) headers.set("Authorization", `Bearer ${accessToken}`);
  let response = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" });
  if (response.status === 401 && !retried && !path.startsWith("/api/auth/") && refreshHandler && await refreshHandler()) {
    return apiRequest<T>(path, init, true);
  }
  if (!response.ok) throw new Error((await response.text()) || `Request failed with ${response.status}`);
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}

export const getRequest = <T>(path: string, _legacyUserId?: string) => apiRequest<T>(path);
export const postRequest = <T>(path: string, body: unknown, _legacyUserId?: string) => apiRequest<T>(path, { method: "POST", body: JSON.stringify(body) });
