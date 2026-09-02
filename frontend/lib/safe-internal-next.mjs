const DEFAULT_NEXT = "/diagnosis";
const CURRENT_ORIGIN = "http://learning-system.local";


export function safeInternalNext(value) {
  if (typeof value !== "string" || !value) return DEFAULT_NEXT;
  if (
    !value.startsWith("/")
    || value.startsWith("//")
    || value.includes("\\")
    || value.includes("\uFFFD")
  ) {
    return DEFAULT_NEXT;
  }
  try {
    const parsed = new URL(value, CURRENT_ORIGIN);
    if (parsed.origin !== CURRENT_ORIGIN || !parsed.pathname.startsWith("/")) {
      return DEFAULT_NEXT;
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return DEFAULT_NEXT;
  }
}
