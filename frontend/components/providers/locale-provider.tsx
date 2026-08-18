"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { DEFAULT_LOCALE, LOCALES, localeForIntl, translate, translateStatus } from "@/lib/i18n.mjs";

export type Locale = (typeof LOCALES)[number];

type LocaleContextValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string, values?: Record<string, string | number>) => string;
  status: (value: string) => string;
  intlLocale: string;
};

const LocaleContext = createContext<LocaleContextValue | null>(null);
const storageKey = "learning-system.locale";

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>(DEFAULT_LOCALE);

  useEffect(() => {
    const stored = window.localStorage.getItem(storageKey);
    if (!stored || !(LOCALES as readonly string[]).includes(stored)) return;
    const frame = window.requestAnimationFrame(() => setLocale(stored as Locale));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale === "en-US" ? "en" : "zh-CN";
    window.localStorage.setItem(storageKey, locale);
  }, [locale]);

  const value = useMemo<LocaleContextValue>(() => ({
    locale,
    setLocale,
    t: (key, values) => translate(locale, key, values),
    status: (value) => translateStatus(locale, value),
    intlLocale: localeForIntl(locale),
  }), [locale]);

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale() {
  const value = useContext(LocaleContext);
  if (!value) throw new Error("useLocale must be used within LocaleProvider");
  return value;
}
