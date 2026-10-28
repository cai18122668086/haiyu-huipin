import assert from "node:assert/strict";
import test from "node:test";

import {
  imageIdentity,
  isProductAsset,
  uniqueProductImages,
} from "../lib/product-image-utils.ts";

test("typed API buckets do not re-filter valid 1688 CDN names", () => {
  const values = [
    "https://cbu01.alicdn.com/img/ibank/detail-gg_dtc.jpg",
    "https://cbu01.alicdn.com/img/ibank/detail-no-dimensions",
    "https://cbu01.alicdn.com/img/ibank/detail-gg_dtc.jpg",
  ];

  assert.equal(
    uniqueProductImages(values, { preserveClassifiedNames: true }).length,
    2,
  );
});

test("classified 1688 asset names are preserved in typed API buckets", () => {
  const values = [
    "https://cbu01.alicdn.com/img/property-detail.jpg",
    "https://cbu01.alicdn.com/img/specification-main.jpg",
    "https://cbu01.alicdn.com/img/sku-color.jpg",
  ];

  assert.equal(
    uniqueProductImages(values, { preserveClassifiedNames: true }).length,
    3,
  );
  assert.equal(uniqueProductImages(values).length, 0);
});

test("variable-size main-image buckets keep all valid images", () => {
  const values = Array.from({ length: 7 }, (_, index) =>
    `https://img.pddpic.com/mms-goods-image/main-${index + 1}.jpg`,
  );

  assert.deepEqual(
    uniqueProductImages(values, { preserveClassifiedNames: true }),
    values,
  );
});
test("CDN query variants do not collapse distinct assets by pathname alone", () => {
  const values = [
    "https://img.example.test/product/image?asset=main-1",
    "https://img.example.test/product/image?asset=main-2",
    "https://img.example.test/product/image?asset=main-1",
  ];

  assert.equal(uniqueProductImages(values).length, 2);
  assert.notEqual(imageIdentity(values[0]), imageIdentity(values[1]));
});

test("explicit UI assets remain filtered while product-sized images remain valid", () => {
  assert.equal(isProductAsset("https://img.example.test/favicon.png"), false);
  assert.equal(isProductAsset("https://img.example.test/O1CN-main.jpg"), true);
  assert.equal(
    isProductAsset(
      "https://img.alicdn.com/imgextra/i2/O1CNplaceholder-0-tps-15-8.svg",
    ),
    false,
  );
});
