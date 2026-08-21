"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { useLocale } from "@/components/providers/locale-provider";

const pageTitleKeys: Record<string, string> = {
  "/ai-config": "meta.config",
  "/assessment": "meta.assessment",
  "/diagnosis": "meta.diagnosis",
  "/login": "meta.login",
  "/path": "meta.path",
  "/progress": "meta.progress",
  "/register": "meta.register",
  "/settings": "meta.settings",
  "/today": "meta.today",
  "/tutor": "meta.tutor",
};

export function LocaleDocumentTitle() {
  const pathname = usePathname();
  const { t } = useLocale();

  useEffect(() => {
    const page = pageTitleKeys[pathname] ? t(pageTitleKeys[pathname]) : t("meta.app");
    document.title = pathname === "/" ? page : t("meta.title", { page, app: t("meta.app") });
  }, [pathname, t]);

  return null;
}
