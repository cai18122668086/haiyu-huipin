import assert from "node:assert/strict";
import test from "node:test";
import {
  MAIN_IMAGE_SPEED_OPTIONS,
  mainImageDraftSpeed,
  mainImageHistoryLabel,
  mainImageSellingPoints,
  mainImageSpeed,
  mainImageSpeedSettings,
  mainImageUsesCompactPlan,
} from "../lib/domestic-main-image-speed.ts";

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

test("new creation offers original and existing native B without extra configuration", () => {
  assert.equal(mainImageSpeed("native_fast"), "native_fast");
  assert.deepEqual(MAIN_IMAGE_SPEED_OPTIONS.map(([value, title]) => [value, title]), [
    ["standard", "原版"],
    ["native_fast", "快速B·原生精简"],
  ]);
  assert.deepEqual(mainImageSpeedSettings("native_fast"), { main_image_speed: "native_fast" });
});

test("native and compact history plans use their own persisted contracts for display", () => {
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

test("retired A drafts map to B without changing saved task modes", () => {
  assert.equal(mainImageDraftSpeed(undefined), "standard");
  assert.equal(mainImageDraftSpeed("fast"), "native_fast");
  assert.equal(mainImageDraftSpeed("native_fast"), "native_fast");
  assert.equal(mainImageSpeed("fast"), "fast");
  assert.equal(mainImageHistoryLabel("fast"), "快速A·历史版本");
  assert.equal(mainImageHistoryLabel("native_fast"), "快速B·原生精简");
});

test("selling points preserve authored wording and punctuation on each line", () => {
  const authored = " 规格：400ml，每套 2 件 \r\n用法：按原包装说明使用\n套餐包含：正装 1 瓶，便携装 1 支\n\n";
  assert.deepEqual(mainImageSellingPoints(authored), [
    "规格：400ml，每套 2 件",
    "用法：按原包装说明使用",
    "套餐包含：正装 1 瓶，便携装 1 支",
  ]);
  assert.deepEqual(mainImageSellingPoints("未知赠送关系、数量暂不填写"), ["未知赠送关系、数量暂不填写"]);
  assert.deepEqual(mainImageSellingPoints(" \n\n"), []);
});
