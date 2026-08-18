import type { Metadata } from "next";

import { AiConfigConsole } from "@/features/ai-config/ai-config-console";

export const metadata: Metadata = { title: "AI configuration | Adaptive Tutor" };

export default function Page() {
  return <AiConfigConsole />;
}
