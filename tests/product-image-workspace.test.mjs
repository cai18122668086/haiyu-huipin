import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const root = process.cwd();
const utf8 = (file) => readFile(path.join(root, file), "utf8");

test("dynamic product-image route renders one shared workspace for all presets", async () => {
  const route = await utf8("app/workspace/product-image/[preset]/page.tsx");
  assert.match(route, /PRODUCT_IMAGE_FEATURES\.map/);
  assert.match(route, /getProductImageFeatureByPresetSlug\(preset\)/);
  assert.match(route, /<ProductImageWorkspace key=\{preset\} preset=\{preset\}/);
  assert.doesNotMatch(route, /switch\s*\(preset\)/);
});

test("workspace uploads role-based assets and submits a bounded product_image job", async () => {
  const workspace = await utf8("components/product-image-workspace.tsx");
  for (const dependency of [
    "useWorkspace",
    "useUploadAsset",
    "useJob",
    "useCreateJob",
    "useRuntimeStatus",
    "resolveRuntimeCapability",
    "selectJobResultAssets",
    "ResultGrid",
  ]) {
    assert.match(workspace, new RegExp(dependency));
  }
  assert.match(workspace, /type: "product_image"/);
  assert.match(workspace, /product_asset_ids: productAssetIds/);
  assert.match(workspace, /reference_asset_ids: referenceAssetIds/);
  assert.match(workspace, /task_type: feature\.taskPreset/);
  assert.match(workspace, /aspect_ratio: aspectRatio/);
  assert.match(workspace, /image_count: imageCount/);
  assert.match(workspace, /instruction: instruction\.trim\(\)/);
  assert.match(workspace, /target_color: targetColor\.trim\(\)/);
  assert.match(workspace, /targetColorMissing/);
  assert.match(workspace, /\[1, 2, 3, 4\]/);
  assert.match(workspace, /workflow\.canSubmit/);
  assert.match(workspace, /disabled=\{!canSubmit\}/);
  assert.doesNotMatch(workspace, /\bfetch\s*\(/, "workspace must use the shared API client");
  assert.doesNotMatch(workspace, /apiClient/, "workspace must use the shared create-job hook");
  assert.doesNotMatch(workspace, /用于约束构图、留白和渠道风格/, "paired selectors must stay visually aligned");
});

test("asset picker prevents cross-role duplication and result grid uses deliverable assets", async () => {
  const picker = await utf8("components/asset-library-picker.tsx");
  const results = await utf8("components/result-grid.tsx");
  const workspace = await utf8("components/product-image-workspace.tsx");

  assert.match(picker, /excludedIds\.includes\(asset\.id\)/);
  assert.match(picker, /lifecycle_status === "ready"/);
  assert.match(picker, /media_kind === "image"/);
  assert.match(workspace, /excludedIds=\{referenceAssetIds\}/);
  assert.match(workspace, /excludedIds=\{productAssetIds\}/);
  assert.match(workspace, /selectJobResultAssets\(job\.data\)/);
  assert.match(results, /JobResultImage/);
  assert.match(results, /IMAGE-\$\{String\(asset\.ordinal \+ 1\)\.padStart\(3, "0"\)\}/);
});

test("tools directory is registry driven and keeps one primary action per card", async () => {
  const page = await utf8("app/tools/page.tsx");
  assert.match(page, /FEATURE_REGISTRY\.filter/);
  assert.match(page, /FEATURE_CATEGORY_LABELS\[category\]/);
  assert.match(page, /立即制作/);
  assert.match(page, /href=\{feature\.route\}/);
});
