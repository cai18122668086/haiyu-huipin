import { SITE_ORIGIN } from "@/lib/seo/pages";
export function GET() {
  // Public login pages remain crawlable so their noindex can be read.
  return new Response([
    "User-agent: *",
    "Allow: /",
    "Disallow: /api/",
    "Disallow: /admin",
    "Disallow: /diagnostics/",
    "",
    `Sitemap: ${SITE_ORIGIN}/sitemap.xml`,
    "",
  ].join("\n"), { headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "public, max-age=300, s-maxage=3600" } });
}

