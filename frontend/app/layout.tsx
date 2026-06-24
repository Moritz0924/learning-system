import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "自适应 AI 应用开发私教",
  description: "学习路径、AI 讲师、测验与计划调整工作台"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
