import assert from "node:assert/strict";
import test from "node:test";
import { MAIN_IMAGE_SPEED_OPTIONS, mainImageSpeed, mainImageSpeedSettings } from "../lib/domestic-main-image-speed.ts";

test("only original and retained native B are selectable and restored", () => {
  assert.deepEqual(MAIN_IMAGE_SPEED_OPTIONS.map(([value]) => value), ["standard", "native_fast"]);
  for (const value of [undefined, null, "standard", "fast", "parallel_v20", {}, true]) assert.equal(mainImageSpeed(value), "standard");
  assert.equal(mainImageSpeed("native_fast"), "native_fast");
});
test("standard preserves legacy request settings while B opts in explicitly", () => {
  const legacy = { product_name: "商品", auto_start_after_plan: true };
  assert.deepEqual({ ...legacy, ...mainImageSpeedSettings("standard") }, legacy);
  assert.deepEqual(mainImageSpeedSettings("native_fast"), { main_image_speed: "native_fast" });
});
