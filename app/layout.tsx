import type { Metadata } from "next";
import { Providers } from "./providers";
import "./globals.css";
import "./focus-inspired.css";
import "./haiyu-theme.css";
import "./haiyu-surfaces.css";
import "./haiyu-workspaces.css";
import "./haiyu-forms.css";
import "./haiyu-readability.css";
import "./showcase-cases.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://huipin.haiyushuke.com"),
  title: { default: "海宇绘品｜电商图片工作台", template: "%s｜海宇绘品" },
  description: "从单张局部精修到整套商品详情，面向电商品牌团队的图片创作工作台。",
  icons: {
    icon: { url: "/brand/haiyu-icon-folded-h.png", type: "image/png", sizes: "1254x1254" },
    shortcut: "/brand/haiyu-icon-folded-h.png",
    apple: "/brand/haiyu-icon-folded-h.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
