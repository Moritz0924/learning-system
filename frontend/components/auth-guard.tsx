"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

import { useAuth } from "@/components/providers/auth-provider";
import { useLocale } from "@/components/providers/locale-provider";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const { t } = useLocale();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (status === "anonymous") router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [pathname, router, status]);

  if (status !== "authenticated") {
    return <main className="grid min-h-screen place-items-center bg-[#f7faf9] text-sm text-muted">{t("auth.checkingSession")}</main>;
  }
  return <>{children}</>;
}
