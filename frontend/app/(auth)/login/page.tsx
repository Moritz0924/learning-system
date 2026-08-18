"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/components/providers/auth-provider";
import { LanguageToggle } from "@/components/language-toggle";
import { useLocale } from "@/components/providers/locale-provider";

export default function LoginPage() {
  const { login } = useAuth();
  const { t } = useLocale();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await login({ email, password });
      router.replace("/diagnosis");
    } catch {
      setError(t("auth.invalidCredentials"));
    } finally {
      setSubmitting(false);
    }
  }

  return <main className="relative grid min-h-screen place-items-center bg-[#f7faf9] p-6"><div className="absolute right-5 top-5"><LanguageToggle /></div><form onSubmit={submit} className="w-full max-w-md rounded-xl border border-line bg-white p-7 shadow-material"><h1 className="text-2xl font-semibold">{t("auth.loginTitle")}</h1><p className="mt-2 text-sm text-muted">{t("auth.loginDescription")}</p>{error && <p role="alert" className="mt-4 text-sm text-coral">{error}</p>}<label className="mt-5 block text-sm">{t("auth.email")}<input data-testid="login-email" required type="email" value={email} onChange={(event) => setEmail(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line px-3" /></label><label className="mt-4 block text-sm">{t("auth.password")}<input data-testid="login-password" required type="password" value={password} onChange={(event) => setPassword(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line px-3" /></label><button data-testid="login-submit" disabled={submitting} className="mt-6 h-10 w-full rounded-lg bg-teal font-semibold text-white disabled:opacity-60">{submitting ? t("auth.loggingIn") : t("auth.login")}</button><p className="mt-4 text-sm text-muted">{t("auth.noAccount")} <Link className="text-teal" href="/register">{t("auth.register")}</Link></p></form></main>;
}
