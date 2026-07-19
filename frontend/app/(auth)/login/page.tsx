"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/components/providers/auth-provider";

export default function LoginPage() {
  const { login } = useAuth();
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
      setError("邮箱或密码不正确。");
    } finally {
      setSubmitting(false);
    }
  }

  return <main className="grid min-h-screen place-items-center bg-[#f7faf9] p-6"><form onSubmit={submit} className="w-full max-w-md rounded-xl border border-line bg-white p-7 shadow-material"><h1 className="text-2xl font-semibold">登录学习系统</h1><p className="mt-2 text-sm text-muted">使用账号继续你的私人学习路径。</p>{error && <p role="alert" className="mt-4 text-sm text-coral">{error}</p>}<label className="mt-5 block text-sm">邮箱<input data-testid="login-email" required type="email" value={email} onChange={(event) => setEmail(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line px-3" /></label><label className="mt-4 block text-sm">密码<input data-testid="login-password" required type="password" value={password} onChange={(event) => setPassword(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line px-3" /></label><button data-testid="login-submit" disabled={submitting} className="mt-6 h-10 w-full rounded-lg bg-teal font-semibold text-white disabled:opacity-60">{submitting ? "登录中…" : "登录"}</button><p className="mt-4 text-sm text-muted">还没有账号？ <Link className="text-teal" href="/register">注册</Link></p></form></main>;
}
