import type { Metadata } from "next";
import { PathPage } from "@/components/learning-pages";

export const metadata: Metadata = {
  title: "学习路径 | 自适应 AI 应用开发私教"
};

export default function Page() {
  return <PathPage />;
}
