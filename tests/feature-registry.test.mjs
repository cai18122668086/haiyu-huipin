import assert from "node:assert/strict";
import test from "node:test";

import {
  FEATURE_GROUPS,
  FEATURE_REGISTRY,
  PRODUCT_IMAGE_FEATURES,
  getFeatureById,
  getFeatureByRoute,
  getProductImageFeatureByPresetSlug,
} from "../lib/feature-registry.ts";

test("feature registry exposes the complete first-phase tool catalog", () => {
  assert.equal(FEATURE_REGISTRY.length, 16);
  assert.deepEqual(
    FEATURE_GROUPS.map((group) => [group.category, group.features.length]),
    [
      ["quick_edit", 5],
      ["product_image", 7],
      ["commerce_pack", 4],
    ],
  );
  assert.equal(getFeatureById("local-edit")?.jobType, "local_edit");
  assert.equal(getFeatureById("detail-pack")?.route, "/workspace/detail-pack");
  assert.equal(getFeatureByRoute("/tools"), undefined);
  assert.equal(getFeatureByRoute("/workspace/reference-remix")?.jobType, "reference_remix");
  assert.equal(getFeatureByRoute("/workspace/model-pose-transfer")?.jobType, "model_pose_transfer");
  assert.equal(getFeatureById("model-pose-transfer")?.skillId, "ecommerce-model-pose-transfer");
  assert.equal(getFeatureByRoute("/workspace/product-image/change-color/")?.jobType, "product_image");
  assert.equal(getFeatureById("amazon-visual-suite")?.completionPolicy, "amazon_21_of_21");
  assert.equal(getFeatureById("domestic-main-image")?.completionPolicy, "aurora_six_page_reviewed_set");
});

test("eight product-image routes share one job type, skill, and workspace template", () => {
  assert.equal(PRODUCT_IMAGE_FEATURES.length, 8);
  assert.equal(new Set(PRODUCT_IMAGE_FEATURES.map((feature) => feature.taskPreset)).size, 8);
  assert.equal(new Set(PRODUCT_IMAGE_FEATURES.map((feature) => feature.route)).size, 8);
  for (const feature of PRODUCT_IMAGE_FEATURES) {
    assert.equal(feature.jobType, "product_image");
    assert.equal(feature.skillId, "xinghe-wanneng-shengtu-3-0");
    assert.equal(feature.workspaceTemplate, "product_image");
    assert.equal(feature.route, `/workspace/product-image/${feature.presetSlug}`);
    assert.equal(
      getProductImageFeatureByPresetSlug(feature.presetSlug)?.taskPreset,
      feature.taskPreset,
    );
  }
});

test("product-image registry includes the exact agreed preset set", () => {
  assert.deepEqual(
    new Set(PRODUCT_IMAGE_FEATURES.map((feature) => feature.taskPreset)),
    new Set([
      "scene_main",
      "white_background",
      "model_image",
      "change_model",
      "try_on",
      "change_scene",
      "change_background",
      "change_color",
    ]),
  );
  assert.equal(getFeatureById("change-color")?.inputHint.includes("颜色参考"), true);
  assert.equal(getFeatureById("creative-image")?.outputHint, "默认 1 张，最多 12 张");
});
