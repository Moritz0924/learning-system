"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { apiRequest, setAccessToken, setRefreshHandler } from "../../lib/api";

type User = { id: string; email: string; display_name: string; role: string; status: string };
type Token = { access_token: string; user: User };
type Value = { status: "bootstrapping" | "authenticated" | "anonymous"; user: User | null; login(input: {email:string;password:string}): Promise<void>; register(input: {email:string;password:string;display_name:string}): Promise<void>; logout(): Promise<void>; refresh(): Promise<string | null> };
const AuthContext = createContext<Value | null>(null);
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Value["status"]>("bootstrapping"); const [user, setUser] = useState<User | null>(null); const flight = useRef<Promise<string | null> | null>(null);
  const apply = useCallback((token: Token) => { setAccessToken(token.access_token); setUser(token.user); setStatus("authenticated"); return token.access_token; }, []);
  const refresh = useCallback(async () => { if (flight.current) return flight.current; flight.current = apiRequest<Token>("/api/auth/refresh", {method:"POST"}).then(apply).catch(() => { setAccessToken(null); setUser(null); setStatus("anonymous"); return null; }).finally(() => { flight.current=null; }); return flight.current; }, [apply]);
  useEffect(() => { setRefreshHandler(refresh); refresh(); return () => setRefreshHandler(null); }, [refresh]);
  const value = useMemo<Value>(() => ({ status, user, refresh, login: async input => { apply(await apiRequest<Token>("/api/auth/login", {method:"POST",body:JSON.stringify(input)})); }, register: async input => { apply(await apiRequest<Token>("/api/auth/register", {method:"POST",body:JSON.stringify(input)})); }, logout: async () => { await apiRequest("/api/auth/logout", {method:"POST"}); setAccessToken(null); setUser(null); setStatus("anonymous"); } }), [apply, refresh, status, user]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
export function useAuth() { const value = useContext(AuthContext); if (!value) throw new Error("useAuth must be used within AuthProvider"); return value; }
