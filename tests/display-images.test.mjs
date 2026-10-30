import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import test from "node:test";

const manifest = JSON.parse(await readFile("lib/display-images.json", "utf8"));
const hero = ["/brand/haiyu-logo-folded-h.png", "/images/cases/olay-20261030/replica-07.png", "/images/cases/platform-v3/background-result.png", "/images/cases/history-20261030/asset_a47a47f308464d7ea2e928e8cc9f5c0c.png"];

test("display derivatives preserve original delivery files and remain within the hero budget", async () => {
  let budget = 0;
  for (const [original, item] of Object.entries(manifest)) {
    const data = await readFile(`public${original}`);
    assert.equal(createHash("sha256").update(data).digest("hex"), item.originalSha256);
    for (const image of item.variants) {
      const bytes = await readFile(`public${image.src}`);
      assert.equal(bytes.length, image.bytes);
      assert.equal(bytes.toString("ascii", 8, 12), "WEBP");
    }
    if (hero.includes(original)) budget += Math.max(...item.variants.map(x => x.bytes));
  }
  assert.ok(budget < 1_000_000, `worst-case hero derivatives: ${budget} bytes`);
  assert.ok(Math.max(...manifest[hero[0]].variants.map(x=>x.bytes)) < 50_000);
});
