import assert from "node:assert/strict";
import test from "node:test";
import { mainImageSpeed, mainImageSpeedSettings } from "../lib/domestic-main-image-speed.ts";

test("main image history defaults to standard and restores only explicit fast", () => {
  for (const value of [undefined, null, "standard", "parallel_v20", {}, true]) {
    assert.equal(mainImageSpeed(value), "standard");
  }
  assert.equal(mainImageSpeed("fast"), "fast");
});

test("standard main image leaves legacy request settings unchanged", () => {
  const legacy = { product_name: "商品", auto_start_after_plan: true };
  assert.deepEqual({ ...legacy, ...mainImageSpeedSettings("standard") }, legacy);
  assert.deepEqual({ ...legacy, ...mainImageSpeedSettings("fast") }, {
    ...legacy, main_image_speed: "fast",
  });
});
