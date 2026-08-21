"use client";

import { useLocale } from "@/components/providers/locale-provider";

export function LanguageToggle() {
  const { locale, setLocale, t } = useLocale();
  const options = [
    ["zh-CN", t("language.chinese")],
    ["en-US", t("language.english")],
  ] as const;

  return (
    <div aria-label={t("language.label")} className="inline-flex overflow-hidden rounded-lg border border-line bg-white text-xs font-semibold shadow-sm">
      {options.map(([value, label]) => (
        <button
          key={value}
          type="button"
          aria-pressed={locale === value}
          className={`h-8 px-2.5 transition-colors ${locale === value ? "bg-teal text-white" : "text-muted hover:bg-[#f1f6f6]"}`}
          onClick={() => setLocale(value)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
