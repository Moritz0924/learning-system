import type { Metadata } from "next";
import { TodayPage } from "@/components/learning-pages";

export const metadata: Metadata = {
  title: "今日学习 | 自适应 AI 应用开发私教"
};

export default function Page() {
  return <TodayPage />;
}
