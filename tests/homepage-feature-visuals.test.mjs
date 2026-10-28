import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { FEATURE_REGISTRY } from "../lib/feature-registry.ts";
import {
  HOME_FEATURE_VISUALS,
} from "../lib/home-feature-visuals.ts";

const root = process.cwd();
const assetDirectory = path.join(root, "public", "images", "homepage-features", "native-v2-webp");
const utf8 = (file) => readFile(path.join(root, file), "utf8");

const EXPECTED_FEATURE_IDS = [
  "local-edit",
  "reference-remix",
  "one-click-replica",
  "detail-pack",
  "domestic-main-image",
  "amazon-visual-suite",
];

const EXPECTED_PRIMARY_PREFIXES = [
  "01-masked",
  "02-background",
  "03-remix",
  "04-detail",
  "05-domestic",
  "06-amazon",
];

const EXPECTED_FILES = EXPECTED_PRIMARY_PREFIXES.flatMap((prefix) => [
  `${prefix}-primary-desktop.webp`,
  `${prefix}-primary-tablet.webp`,
  `${prefix}-primary-mobile.webp`,
]);

const EXPECTED_INPUT_FILES = ["01", "02", "03", "04", "05", "06"].flatMap((prefix) => [
  `${prefix}-input-desktop.webp`,
  `${prefix}-input-tablet.webp`,
  `${prefix}-input-mobile.webp`,
]);

function imageDimensions(buffer, file) {
  if (file.endsWith(".png")) {
    assert.deepEqual([...buffer.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
    return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
  }

  assert.equal(buffer.toString("ascii", 0, 4), "RIFF");
  assert.equal(buffer.toString("ascii", 8, 12), "WEBP");
  const format = buffer.toString("ascii", 12, 16);

  if (format === "VP8X") {
    return {
      width: 1 + buffer.readUIntLE(24, 3),
      height: 1 + buffer.readUIntLE(27, 3),
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

  throw new Error(`Unsupported image header for ${file}: ${format}`);
}

function mappedImages() {
  return Object.values(HOME_FEATURE_VISUALS).flatMap((visual) => [
    ...Object.values(visual.primary),
    ...Object.values(visual.input),
  ]);
}

test("the homepage grid covers all one-click tasks and their registered routes", () => {
  const featured = FEATURE_REGISTRY.filter((feature) => feature.featured);
  assert.deepEqual(
    featured.map((feature) => feature.id),
    EXPECTED_FEATURE_IDS,
  );
  assert.deepEqual(Object.keys(HOME_FEATURE_VISUALS), EXPECTED_FEATURE_IDS.filter(id => id !== "one-click-replica"));
  assert.deepEqual(
    featured.map((feature) => feature.route),
    [
      "/workspace/local-edit",
      "/workspace/reference-remix",
      "/style-replica",
      "/workspace/detail-pack",
      "/workspace/domestic-main-image",
      "/workspace/amazon-visual-suite",
    ],
  );
});

test("all responsive production assets use supported formats with declared stable dimensions", async () => {
  const files = (await readdir(assetDirectory)).sort();
  assert.deepEqual(files, [...EXPECTED_FILES, ...EXPECTED_INPUT_FILES].sort());
  assert.equal(files.some((file) => file.toLowerCase().endsWith(".gif")), false);

  const images = mappedImages();
  assert.equal(images.length, 30);
  assert.deepEqual(
    images.map((image) => path.basename(image.src)).sort(),
    [...EXPECTED_FILES, ...EXPECTED_INPUT_FILES].filter(file => !file.startsWith("02-")).sort(),
  );

  for (const image of images) {
    const buffer = await readFile(path.join(root, "public", image.src));
    assert.deepEqual(imageDimensions(buffer, path.basename(image.src)), {
      width: image.width,
      height: image.height,
    });
  }
});

test("homepage cards expose hover, keyboard, mobile-once, and reduced-motion states", async () => {
  const component = await utf8("components/tool-directory.tsx");
  const css = await utf8("app/focus-inspired.css");
  const mapping = await utf8("lib/home-feature-visuals.ts");
  const productionSource = `${component}\n${css}\n${mapping}`;

  assert.match(component, /data-feature-visual=\{HOME_FEATURE_VISUALS\[tool\.id\]\?\.kind\}/);
  assert.match(component, /new IntersectionObserver/);
  assert.match(component, /classList\.add\("is-mobile-played"\)/);
  assert.match(component, /observer\.unobserve\(entry\.target\)/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /:focus-within/);
  assert.match(css, /\.is-mobile-played/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(css, /home-feature-card__image--detail-cover/);
  assert.match(css, /animation:\s*none !important/);
  assert.match(css, /grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/);
  assert.match(css, /grid-template-rows: repeat\(2, var\(--home-feature-grid-row\)\)/);
  assert.doesNotMatch(css, /home-feature-card:nth-child/);
  assert.doesNotMatch(component, /home-aurora-entry/);
  assert.doesNotMatch(component, /home-feature-card__remix-reference/);
  assert.doesNotMatch(productionSource, /\.gif\b/i);
});
