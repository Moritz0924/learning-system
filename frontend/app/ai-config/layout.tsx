import { AuthGuard } from "@/components/auth-guard";

export default function AiConfigLayout({ children }: { children: React.ReactNode }) {
  return <AuthGuard>{children}</AuthGuard>;
}
