import { SITE_ORIGIN, SEO_PAGES } from "@/lib/seo/pages";

export function StructuredData({ data }: { data: object | object[] }) {
  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(data).replace(/</g, "\\u003c") }} />;
}
export function SiteStructuredData() {
  return <StructuredData data={[
    { "@context": "https://schema.org", "@type": "Organization", "@id": SITE_ORIGIN + "/#organization", name: "海宇数科（广东横琴）科技有限公司", url: "https://www.haiyushuke.com/", logo: SITE_ORIGIN + "/brand/haiyu-icon-folded-h.png" },
    { "@context": "https://schema.org", "@type": "WebSite", "@id": SITE_ORIGIN + "/#website", name: "海宇绘品", url: SITE_ORIGIN + "/", inLanguage: "zh-CN", publisher: { "@id": SITE_ORIGIN + "/#organization" } },
  ]} />;
}
export function ToolsStructuredData() {
  return <StructuredData data={{ "@context": "https://schema.org", "@type": "ItemList", name: "海宇绘品AI电商图片工具", itemListElement: SEO_PAGES.filter(page => page.tool).map((page, index) => ({ "@type": "ListItem", position: index + 1, name: page.h1, url: SITE_ORIGIN + page.path })) }} />;
}

