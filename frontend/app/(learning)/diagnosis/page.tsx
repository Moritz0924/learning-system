import type { Metadata } from "next";
import { DiagnosisPage } from "@/components/learning-pages";

export const metadata: Metadata = {
  title: "入学诊断 | 自适应 AI 应用开发私教"
};

export default function Page() {
  return <DiagnosisPage />;
}
