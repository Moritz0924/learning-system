const DEFAULT_NEXT = "/diagnosis";
const CURRENT_ORIGIN = "http://learning-system.local";


export function safeInternalNext(value) {
  if (typeof value !== "string" || !value) return DEFAULT_NEXT;
  let decoded;
  try {
    decoded = decodeURIComponent(value);
  } catch {
    return DEFAULT_NEXT;
  }
  if (
    !decoded.startsWith("/")
    || decoded.startsWith("//")
    || decoded.includes("\\")
  ) {
    return DEFAULT_NEXT;
  }
  try {
    const parsed = new URL(decoded, CURRENT_ORIGIN);
    if (parsed.origin !== CURRENT_ORIGIN || !parsed.pathname.startsWith("/")) {
      return DEFAULT_NEXT;
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return DEFAULT_NEXT;
  }
}
