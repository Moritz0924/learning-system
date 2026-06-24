import type { Metadata } from "next";
import { SettingsPage } from "@/components/learning-pages";

export const metadata: Metadata = {
  title: "设置 | 自适应 AI 应用开发私教"
};

export default function Page() {
  return <SettingsPage />;
}
