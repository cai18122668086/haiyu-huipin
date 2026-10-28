import type { Metadata } from "next";
import { headers } from "next/headers";
import { Providers } from "./providers";
import "./globals.css";
import "./focus-inspired.css";
import "./haiyu-theme.css";
import "./haiyu-surfaces.css";
import "./haiyu-workspaces.css";
import "./haiyu-forms.css";
import "./haiyu-readability.css";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host =
    requestHeaders.get("x-forwarded-host") ??
    requestHeaders.get("host") ??
    "localhost:3000";
  const protocol =
    requestHeaders.get("x-forwarded-proto") ??
    (host.startsWith("localhost") ? "http" : "https");
  const previewImage = new URL("/og.png?v=folded-h-20261027", `${protocol}://${host}`).toString();
  const title = "海宇绘品｜电商图片工作台";
  const description =
    "从单张局部精修到整套商品详情，面向电商品牌团队的图片创作工作台。";

  return {
    title: {
      default: title,
      template: "%s｜海宇绘品",
    },
    description,
    icons: {
      icon: { url: "/brand/haiyu-icon-folded-h.png", type: "image/png", sizes: "1254x1254" },
      shortcut: "/brand/haiyu-icon-folded-h.png",
      apple: "/brand/haiyu-icon-folded-h.png",
    },
    openGraph: {
      title,
      description,
      type: "website",
      images: [{ url: previewImage, alt: "海宇绘品电商图片工作台" }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [previewImage],
    },
  };
}

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
