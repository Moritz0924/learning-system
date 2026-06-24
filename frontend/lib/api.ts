const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {})
    }
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function getRequest<T>(path: string, userId?: string): Promise<T> {
  return apiRequest<T>(path, userId ? { headers: { "X-User-Id": userId } } : undefined);
}

export async function postRequest<T>(path: string, body: unknown, userId?: string): Promise<T> {
  return apiRequest<T>(path, {
    method: "POST",
    headers: userId ? { "X-User-Id": userId } : undefined,
    body: JSON.stringify(body)
  });
}
