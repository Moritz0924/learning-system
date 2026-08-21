import type { Metadata } from "next";

import { AiConfigConsole } from "@/features/ai-config/ai-config-console";

export const metadata: Metadata = { title: "AI 配置 | 自适应学习导师" };

export default function Page() {
  return <AiConfigConsole />;
}
