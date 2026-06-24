import type { Metadata } from "next";
import { AssessmentPage } from "@/components/learning-pages";

export const metadata: Metadata = {
  title: "测验 | 自适应 AI 应用开发私教"
};

export default function Page() {
  return <AssessmentPage />;
}
