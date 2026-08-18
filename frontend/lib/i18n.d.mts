export const DEFAULT_LOCALE: "zh-CN";
export const LOCALES: readonly ["zh-CN", "en-US"];

export function translate(
  locale: "zh-CN" | "en-US",
  key: string,
  values?: Record<string, string | number>,
): string;

export function translateStatus(locale: "zh-CN" | "en-US", value: string): string;
export function translateModelTestFailure(locale: "zh-CN" | "en-US", code: string | null): string;
export function translateEnum(locale: "zh-CN" | "en-US", prefix: string, value: string): string;
export function localeForIntl(locale: "zh-CN" | "en-US"): "zh-CN" | "en-US";
