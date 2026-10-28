import assert from "node:assert/strict";
import test from "node:test";

import { FEATURE_REGISTRY } from "../lib/feature-registry.ts";
import { searchFeatures } from "../lib/feature-search.ts";

const expectedRoutes = new Map([
  ["一句话改图", "/workspace/local-edit"],
  ["耳机局部精修", "/workspace/local-edit"],
  ["耳机局部修改", "/workspace/local-edit"],
  ["商品局部修图", "/workspace/local-edit"],
  ["想修改图片的一小部分", "/workspace/local-edit"],
  ["商品换色", "/workspace/product-image/change-color"],
  ["耳机换颜色", "/workspace/product-image/change-color"],
  ["更换商品背景", "/workspace/product-image/change-background"],
  ["给商品换背景", "/workspace/product-image/change-background"],
  ["亚马逊主图", "/workspace/amazon-visual-suite"],
  ["商品详情图", "/workspace/detail-pack"],
  ["一整套电商图", "/workspace/detail-pack"],
  ["商品试穿", "/workspace/product-image/try-on"],
  ["模特换姿势", "/workspace/model-pose-transfer"],
  ["电商白底图", "/workspace/product-image/white-background"],
]);

for (const [query, route] of expectedRoutes) {
  test(`homepage search maps ${query} to the intended feature`, () => {
    const [match] = searchFeatures(query, FEATURE_REGISTRY);
    assert.ok(match, `expected a result for ${query}`);
    assert.equal(match.feature.route, route);
    assert.ok(match.matchReason.length > 0);
  });
}

test("homepage search normalizes case, punctuation, and extra spaces", () => {
  const [match] = searchFeatures("  Amazon   主图！ ", FEATURE_REGISTRY);
  assert.equal(match?.feature.id, "amazon-visual-suite");
});

test("homepage search returns an empty list for unrelated input", () => {
  assert.deepEqual(searchFeatures("这是一个不存在的功能 xyz", FEATURE_REGISTRY), []);
  assert.deepEqual(searchFeatures("", FEATURE_REGISTRY), []);
});

test("homepage search keeps the strongest intent match ahead of broad keyword matches", () => {
  const [first, second] = searchFeatures("亚马逊主图", FEATURE_REGISTRY);
  assert.equal(first?.feature.id, "amazon-visual-suite");
  assert.notEqual(second?.feature.id, "amazon-visual-suite");
  assert.match(first?.matchReason ?? "", /亚马逊主图/);
});


test("homepage search removes weak broad-keyword matches when a strong intent match exists", () => {
  assert.deepEqual(
    searchFeatures("亚马逊主图", FEATURE_REGISTRY).map((match) => match.feature.id),
    ["amazon-visual-suite"],
  );
  assert.deepEqual(
    searchFeatures("模特换姿势", FEATURE_REGISTRY).map((match) => match.feature.id),
    ["model-pose-transfer"],
  );
});
