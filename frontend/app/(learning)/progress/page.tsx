import type { Metadata } from "next";
import { ProgressPage } from "@/components/learning-pages";

export const metadata: Metadata = {
  title: "进度 | 自适应 AI 应用开发私教"
};

export default function Page() {
  return <ProgressPage />;
}
