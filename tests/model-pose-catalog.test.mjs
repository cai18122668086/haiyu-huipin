import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const catalogRoot = path.join(
  root,
  "backend",
  "catalog_assets",
  "model-pose-transfer",
);
const publicRoot = path.join(root, "public");
const catalog = JSON.parse(
  await readFile(path.join(catalogRoot, "catalog.json"), "utf8"),
);
const expectedMale = Array.from(
  { length: 21 },
  (_, index) => `male-${String(index + 1).padStart(3, "0")}`,
);
const expectedFemaleNumbers = [
  ...Array.from({ length: 33 }, (_, index) => index + 1),
  35,
  36,
  38,
  39,
  40,
  41,
];
const expectedFemale = expectedFemaleNumbers.map(
  (number) => `female-${String(number).padStart(3, "0")}`,
);

function sha256(buffer) {
  return createHash("sha256").update(buffer).digest("hex");
}

function webpDimensions(buffer) {
  assert.equal(buffer.toString("ascii", 0, 4), "RIFF");
  assert.equal(buffer.toString("ascii", 8, 12), "WEBP");
  const format = buffer.toString("ascii", 12, 16);

  if (format === "VP8X") {
    return {
      width: 1 + buffer.readUIntLE(24, 3),
      height: 1 + buffer.readUIntLE(27, 3),
    };
  }
  if (format === "VP8L") {
    const bits = buffer.readUInt32LE(21);
    return {
      width: 1 + (bits & 0x3fff),
      height: 1 + ((bits >> 14) & 0x3fff),
    };
  }
  if (format === "VP8 ") {
    for (let offset = 20; offset < Math.min(buffer.length - 10, 60); offset += 1) {
      if (
        buffer[offset] === 0x9d &&
        buffer[offset + 1] === 0x01 &&
        buffer[offset + 2] === 0x2a
      ) {
        return {
          width: buffer.readUInt16LE(offset + 3) & 0x3fff,
          height: buffer.readUInt16LE(offset + 5) & 0x3fff,
        };
      }
    }
  }
  throw new Error(`Unsupported WebP header: ${format}`);
}

async function assertPublicWebp(src, metadata) {
  assert.match(src, /^\/images\/model-pose-transfer\/(thumbs|previews)\//);
  const buffer = await readFile(path.join(publicRoot, src));
  assert.equal(buffer.byteLength, metadata.byteSize);
  assert.equal(sha256(buffer), metadata.sha256);
  assert.deepEqual(webpDimensions(buffer), {
    width: metadata.width,
    height: metadata.height,
  });
}

test("model pose catalog exposes the exact stable 21 male and 39 female slugs", () => {
  assert.equal(catalog.schemaVersion, "model-pose-catalog-v1");
  assert.equal(catalog.catalogId, "ecommerce-model-pose-transfer");
  assert.equal(catalog.itemCount, 60);
  assert.equal(catalog.items.length, 60);

  const male = catalog.items.filter((item) => item.genderGroup === "male");
  const female = catalog.items.filter((item) => item.genderGroup === "female");
  assert.deepEqual(male.map((item) => item.slug), expectedMale);
  assert.deepEqual(female.map((item) => item.slug), expectedFemale);
  assert.equal(new Set(catalog.items.map((item) => item.slug)).size, 60);
  assert.equal(catalog.items.some((item) => item.slug === "female-034"), false);
  assert.equal(catalog.items.some((item) => item.slug === "female-037"), false);
});

test("catalog points only at public WebP derivatives and keeps originals private", async () => {
  for (const item of catalog.items) {
    assert.equal(item.originalKey, `originals/${item.slug}.png`);
    assert.equal(item.original.filename, `${item.slug}.png`);
    assert.equal(item.original.mimeType, "image/png");
    assert.equal(item.referenceSha256, item.original.sha256);
    assert.doesNotMatch(JSON.stringify(item.thumbnail), /\.png(?:"|$)/i);
    assert.doesNotMatch(JSON.stringify(item.preview), /\.png(?:"|$)/i);

    for (const width of [320, 640]) {
      const suffix = String(width);
      await assertPublicWebp(item.thumbnail[`src${suffix}`], {
        width: item.thumbnail[`width${suffix}`],
        height: item.thumbnail[`height${suffix}`],
        byteSize: item.thumbnail[`byteSize${suffix}`],
        sha256: item.thumbnail[`sha256${suffix}`],
      });
    }
    await assertPublicWebp(item.preview.src, item.preview);
  }

  const publicFiles = await readdir(
    path.join(publicRoot, "images", "model-pose-transfer"),
    { recursive: true },
  );
  assert.equal(publicFiles.some((name) => name.toLowerCase().endsWith(".png")), false);
});

test("private originals have the catalogued hashes, byte sizes, and PNG dimensions", async () => {
  for (const item of catalog.items) {
    const originalPath = path.join(catalogRoot, item.originalKey);
    const buffer = await readFile(originalPath);
    assert.equal(buffer.byteLength, item.original.byteSize);
    assert.equal(sha256(buffer), item.original.sha256);
    assert.deepEqual([...buffer.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
    assert.equal(buffer.readUInt32BE(16), item.original.width);
    assert.equal(buffer.readUInt32BE(20), item.original.height);
  }
});
