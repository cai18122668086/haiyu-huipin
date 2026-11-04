import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { build } from "esbuild";

async function loadModule(entry) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "huipin-seo-"));
  try {
    const outfile = path.join(directory, "module.mjs");
    await build({ entryPoints: [entry], outfile, bundle: true, platform: "node", format: "esm", logLevel: "silent" });
    return await import(pathToFileURL(outfile).href);
  } finally { await rm(directory, { recursive: true, force: true }); }
}

test("private links keep existing relationship tokens and public tool links remain followable", async () => {
  const { linkSeoAttributes } = await loadModule("lib/seo/links.ts");
  for (const href of ["/auth/login?returnTo=%2Ftools", "/jobs/123", "/assets", "/account#orders", "https://huipin.haiyushuke.com/admin/billing"]) {
    assert.equal(linkSeoAttributes(href, "noopener noreferrer").rel, "noopener noreferrer nofollow", href);
  }
  assert.equal(linkSeoAttributes("/assets", "nofollow").rel, "nofollow");
  for (const href of ["/assets-public", "/workspace/product-image/change-color", "/inspiration", "https://elsewhere.example/jobs"]) {
    assert.equal(linkSeoAttributes(href).rel, undefined, href);
  }
  assert.equal(linkSeoAttributes("/style-replica", undefined, "自定义功能说明").title, "自定义功能说明");
});

test("all 20 canonical pages have distinct title and description and preserve the exact site origin", async () => {
  const { SEO_PAGES, SITE_ORIGIN } = await loadModule("lib/seo/pages.ts");
  const { seoMetadata } = await loadModule("lib/seo/metadata.ts");
  assert.equal(SEO_PAGES.length, 20);
  assert.equal(SEO_PAGES.filter(page => page.tool).length, 16);
  assert.equal(new Set(SEO_PAGES.map(page => page.title)).size, 20);
  assert.equal(new Set(SEO_PAGES.map(page => page.description)).size, 20);
  for (const page of SEO_PAGES) {
    const meta = seoMetadata(page.path);
    assert.deepEqual(meta.title, { absolute: page.title });
    assert.equal(meta.alternates.canonical, SITE_ORIGIN + page.path);
    assert.equal(meta.openGraph.url, meta.alternates.canonical);
    assert.ok(page.h1 && page.keywords);
  }
  assert.throws(() => seoMetadata("/workspace/product-image/private"));
});

test("sitemap lists only canonical public pages and robots keeps public tool HTML crawlable", async () => {
  const { GET: sitemap } = await loadModule("app/sitemap.xml/route.ts");
  const { GET: robots } = await loadModule("app/robots.txt/route.ts");
  const xml = await sitemap().text();
  assert.equal((xml.match(/<loc>/g) ?? []).length, 20);
  assert.doesNotMatch(xml, /\/(?:auth|jobs|assets|admin|account)(?:\/|<)/);
  assert.doesNotMatch(xml, /<lastmod>/);
  const rules = await robots().text();
  assert.match(rules, /Sitemap: https:\/\/huipin.haiyushuke.com\/sitemap.xml/);
  assert.doesNotMatch(rules, /Disallow: \/workspace/);
});
