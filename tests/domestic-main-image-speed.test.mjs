import assert from "node:assert/strict";
import test from "node:test";
import { MAIN_IMAGE_SPEED_OPTIONS, mainImageSpeed, mainImageSpeedSettings, mainImageUsesCompactPlan } from "../lib/domestic-main-image-speed.ts";

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


test("native fast restores independently while original fast retains option A", () => {
  assert.equal(mainImageSpeed("native_fast"), "native_fast");
  assert.equal(mainImageSpeed("fast"), "fast");
  assert.deepEqual(MAIN_IMAGE_SPEED_OPTIONS.map(([value, title]) => [value, title]), [
    ["standard", "原版"],
    ["fast", "快速A·整套规划"],
    ["native_fast", "快速B·原生精简"],
  ]);
  assert.deepEqual(mainImageSpeedSettings("native_fast"), { main_image_speed: "native_fast" });
});

test("native and compact plans use their own persisted contracts for display", () => {
  const originalPage = { page_index: 0, page_role: "SEARCH_MAIN_IMAGE_LISTING_COVER", main_selling_point: "真实卖点", composition_and_subject: "原生构图" };
  assert.equal(mainImageUsesCompactPlan({
    product_identity_lock: { product_form: "杯", identity_summary: "原始商品" },
    campaign_style: { graphic_language: "原版风格" }, pages: [originalPage],
    validation: { visual_review: "NOT_REVIEWED" },
  }), false);
  assert.equal(mainImageUsesCompactPlan({
    product_identity_lock: "原始商品", style_lock: "共享风格", pages: [{ visual_plan: "完整画面规划" }],
  }), true);
  assert.equal(mainImageUsesCompactPlan(undefined), false);
  assert.equal(mainImageUsesCompactPlan({ pages: [] }), false);
});
