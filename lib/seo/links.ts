import { getToolSeoPage, SITE_ORIGIN } from "./pages";

export function linkSeoAttributes(href: string, rel?: string, title?: string) {
  let url: URL;
  try { url = new URL(href, SITE_ORIGIN); } catch { return { rel, title }; }
  if (url.origin !== SITE_ORIGIN) return { rel, title };
  const privatePrefixes = ["/auth", "/jobs", "/assets", "/account", "/admin", "/dashboard", "/commerce-crawler"];
  const privateLink = privatePrefixes.some(prefix => url.pathname === prefix || url.pathname.startsWith(prefix + "/"));
  return {
    rel: privateLink ? [...new Set([...(rel?.split(/\s+/).filter(Boolean) ?? []), "nofollow"])].join(" ") : rel,
    title: title ?? getToolSeoPage(url.pathname)?.h1,
  };
}
