import type { Metadata } from "next";
import { TutorPage } from "@/components/learning-pages";

export const metadata: Metadata = {
  title: "AI 讲师 | 自适应 AI 应用开发私教"
};

export default function Page() {
  return <TutorPage />;
}
