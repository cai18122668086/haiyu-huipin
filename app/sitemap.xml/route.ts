import { SEO_PAGES, SITE_ORIGIN } from "@/lib/seo/pages";
export function GET() {
  // No fabricated lastmod: these static page records have no recorded edit dates.
  const entries = SEO_PAGES.map(page => `  <url><loc>${SITE_ORIGIN}${page.path}</loc></url>`).join("\n");
  return new Response(`<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${entries}\n</urlset>\n`, { headers: { "Content-Type": "application/xml; charset=utf-8", "Cache-Control": "public, max-age=300, s-maxage=3600" } });
}
