import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { probeReferenceImage } from "../lib/replica/reference-images.ts";

async function probe({ width = 0, height = 0, error = false, timeout = false }) {
  const image = { naturalWidth: width, naturalHeight: height, onload: null, onerror: null, src: "", referrerPolicy: "" };
  const result = probeReferenceImage("https://img.alicdn.com/product.jpg", { createImage: () => image, timeoutMs: 10 });
  assert.equal(image.referrerPolicy, "no-referrer");
  if (!timeout) (error ? image.onerror : image.onload)?.(new Event(error ? "error" : "load"));
  const status = await result;
  assert.equal(image.onload, null);
  assert.equal(image.onerror, null);
  return status;
}

test("valid square and long product images remain usable", async () => {
  assert.equal(await probe({ width: 1440, height: 1440 }), "ready");
  assert.equal(await probe({ width: 1000, height: 180 }), "ready");
});

test("only successfully decoded undersized images are classified as blank", async () => {
  assert.equal(await probe({ width: 1, height: 1 }), "blank");
  assert.equal(await probe({ width: 1440, height: 1 }), "blank");
  assert.equal(await probe({ error: true }), "unavailable");
  assert.equal(await probe({ timeout: true }), "unavailable");
});

test("all entry documents share the replica image policy across SPA navigation", async () => {
  const caddy = await readFile(new URL("../deploy/Caddyfile.domestic-detail-native", import.meta.url), "utf8");
  const policies = [...caddy.matchAll(/Content-Security-Policy "([^"\n]+)"/g)].map(match => match[1]);
  assert.equal(policies.length, 2);
  for (const policy of policies) {
    const directives = Object.fromEntries(policy.split(";").map(value => value.trim().split(/\s+/)).filter(parts => parts[0]).map(([name, ...values]) => [name, values]));
    assert.deepEqual(directives["img-src"], ["'self'", "data:", "blob:", "https://alicdn.com", "https://*.alicdn.com", "https://taobaocdn.com", "https://*.taobaocdn.com", "https://tbcdn.cn", "https://*.tbcdn.cn", "https://360buyimg.com", "https://*.360buyimg.com"]);
    assert.deepEqual(directives["default-src"], ["'self'"]);
    assert.deepEqual(directives["script-src"], ["'self'", "'unsafe-inline'"]);
    assert.deepEqual(directives["connect-src"], ["'self'", "http://localhost:8765/v1/enrollment", "http://127.0.0.1:8765/v1/enrollment"]);
  }
  assert.equal(policies[0], policies[1]);
});
