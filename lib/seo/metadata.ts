import type { Metadata } from "next";
import { getSeoPage, SITE_ORIGIN } from "./pages";

export function seoMetadata(pathname: string): Metadata {
  const page = getSeoPage(pathname);
  if (!page) throw new Error(`Missing SEO page: ${pathname}`);
  const url = SITE_ORIGIN + page.path;
  return {
    title: { absolute: page.title },
    description: page.description,
    keywords: page.keywords,
    alternates: { canonical: url },
    openGraph: { title: page.title, description: page.description, url, siteName: "海宇绘品", type: "website", locale: "zh_CN", images: [{ url: SITE_ORIGIN + "/og.png?v=folded-h-20261027", alt: "海宇绘品 AI电商图片工作台" }] },
    twitter: { card: "summary_large_image", title: page.title, description: page.description },
  };
}

