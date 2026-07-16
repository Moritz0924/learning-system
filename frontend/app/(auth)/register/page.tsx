"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/components/providers/auth-provider";

export default function RegisterPage() {
  const { register } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await register({ email, password, display_name: displayName });
      router.replace("/diagnosis");
    } catch {
      setError("注册失败：邮箱可能已被使用，或密码不足 12 位。");
    } finally {
      setSubmitting(false);
    }
  }

  return <main className="grid min-h-screen place-items-center bg-[#f7faf9] p-6"><form onSubmit={submit} className="w-full max-w-md rounded-xl border border-line bg-white p-7 shadow-material"><h1 className="text-2xl font-semibold">创建学习账号</h1><p className="mt-2 text-sm text-muted">注册后即可建立你的学习路径。</p>{error && <p role="alert" className="mt-4 text-sm text-coral">{error}</p>}<label className="mt-5 block text-sm">姓名<input data-testid="register-name" required value={displayName} onChange={(event) => setDisplayName(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line px-3" /></label><label className="mt-4 block text-sm">邮箱<input data-testid="register-email" required type="email" value={email} onChange={(event) => setEmail(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line px-3" /></label><label className="mt-4 block text-sm">密码<input data-testid="register-password" required minLength={12} type="password" value={password} onChange={(event) => setPassword(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line px-3" /></label><button data-testid="register-submit" disabled={submitting} className="mt-6 h-10 w-full rounded-lg bg-teal font-semibold text-white disabled:opacity-60">{submitting ? "注册中…" : "注册"}</button><p className="mt-4 text-sm text-muted">已有账号？ <Link className="text-teal" href="/login">登录</Link></p></form></main>;
}
