import { LearningProvider } from "@/components/learning-provider";
import { LearningShell } from "@/components/learning-shell";

export default function LearningLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <LearningProvider>
        <LearningShell>{children}</LearningShell>
      </LearningProvider>
    </AuthGuard>
  );
}
import { AuthGuard } from "@/components/auth-guard";
