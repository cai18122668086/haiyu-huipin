import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  assertEditableMask,
  createBinaryMask,
  mapDisplayPointToRaster,
} from "../lib/mask/mask-utils.ts";
import {
  canRetryJob,
  selectJobInputAssets,
  selectJobIntermediateAssets,
  selectJobReferenceAssets,
  selectJobResultAssets,
} from "../lib/jobs/results.ts";
import {
  jobAssetRoleLabel,
  jobItemStatusLabel,
  jobStageLabel,
} from "../lib/jobs/presentation.ts";

test("display coordinates map to original raster pixels", () => {
  assert.deepEqual(
    mapDisplayPointToRaster(
      260,
      145,
      { left: 10, top: 20, width: 500, height: 250 },
      { width: 2000, height: 1000 },
    ),
    { x: 1000, y: 500 },
  );
  assert.deepEqual(
    mapDisplayPointToRaster(
      -100,
      900,
      { left: 10, top: 20, width: 500, height: 250 },
      { width: 2000, height: 1000 },
    ),
    { x: 0, y: 1000 },
  );
});

test("mask export data is binary black/white with opaque alpha", () => {
  const binary = createBinaryMask(
    new Uint8ClampedArray([
      124, 58, 237, 0,
      124, 58, 237, 1,
      124, 58, 237, 128,
    ]),
    3,
    1,
  );
  assert.deepEqual(
    [...binary.data],
    [
      0, 0, 0, 255,
      255, 255, 255, 255,
      255, 255, 255, 255,
    ],
  );
  assert.equal(binary.selectedPixels, 2);
});

test("empty and full-image masks are rejected", () => {
  assert.throws(
    () => assertEditableMask({ selectedPixels: 0, totalPixels: 4 }),
    /选区为空/,
  );
  assert.throws(
    () => assertEditableMask({ selectedPixels: 4, totalPixels: 4 }),
    /不能授权整张图片/,
  );
  assert.doesNotThrow(() =>
    assertEditableMask({ selectedPixels: 2, totalPixels: 4 }),
  );
});

test("only deliverable output assets become visible results", () => {
  const results = selectJobResultAssets({
    assets: [
      { asset_id: "input", direction: "input", is_deliverable: false, ordinal: 0 },
      { asset_id: "hidden", direction: "output", is_deliverable: false, ordinal: 1 },
      { asset_id: "second", direction: "output", is_deliverable: true, ordinal: 2 },
      { asset_id: "first", direction: "output", is_deliverable: true, ordinal: 1 },
    ],
  });
  assert.deepEqual(results.map((asset) => asset.asset_id), ["first", "second"]);
});

test("input, reference, intermediate, and output assets stay in separate groups", () => {
  const job = {
    assets: [
      { asset_id: "identity", direction: "input", role: "product_identity_authority", is_deliverable: false, ordinal: 2 },
      { asset_id: "product", direction: "input", role: "product_reference", is_deliverable: false, ordinal: 1 },
      { asset_id: "pose", direction: "input", role: "pose_reference", is_deliverable: false, ordinal: 0 },
      { asset_id: "preview", direction: "intermediate", role: "provider_preview", is_deliverable: false, ordinal: 0 },
      { asset_id: "hidden-output", direction: "output", role: "provider_preview", is_deliverable: false, ordinal: 1 },
      { asset_id: "result-2", direction: "output", role: "result", is_deliverable: true, ordinal: 2 },
      { asset_id: "result-1", direction: "output", role: "result", is_deliverable: true, ordinal: 1 },
    ],
  };

  assert.deepEqual(selectJobInputAssets(job).map((asset) => asset.asset_id), ["product", "identity"]);
  assert.deepEqual(selectJobReferenceAssets(job).map((asset) => asset.asset_id), ["pose"]);
  assert.deepEqual(selectJobIntermediateAssets(job).map((asset) => asset.asset_id), ["preview", "hidden-output"]);
  assert.deepEqual(selectJobResultAssets(job).map((asset) => asset.asset_id), ["result-1", "result-2"]);
  assert.deepEqual(selectJobResultAssets({ assets: job.assets.filter((asset) => asset.direction === "input") }), []);
});

test("retry visibility follows allowed_actions only", () => {
  assert.equal(canRetryJob({ allowed_actions: [] }), false);
  assert.equal(canRetryJob({ allowed_actions: ["retry"] }), true);
  assert.equal(canRetryJob(undefined), false);
});


test("job detail displays stages without fabricating unknown values", () => {
  assert.equal(jobStageLabel("generating", "processing"), "\u751f\u6210\u4e2d");
  assert.equal(jobStageLabel("provider_specific_stage", "processing"), "\u9636\u6bb5\uff1aprovider_specific_stage");
  assert.equal(jobStageLabel(null, "completed"), "\u5df2\u5b8c\u6210");
});

test("job detail labels item statuses and asset roles from backend values", () => {
  assert.equal(jobItemStatusLabel("failed"), "\u5931\u8d25");
  assert.equal(jobAssetRoleLabel("pose_reference"), "\u59ff\u52bf\u53c2\u8003");
  assert.equal(jobAssetRoleLabel("provider_output"), "provider output");
});

test("job record list uses the summary contract instead of per-card detail queries", async () => {
  const source = await readFile("features/jobs/components/job-record-cover.tsx", "utf8");
  assert.match(source, /primary_output_asset_id/);
  assert.match(source, /output_asset_count/);
  assert.doesNotMatch(source, /useJob\(/);
});

test("job detail renders explicit output, empty, download, and safe failure states", async () => {
  const source = await readFile("features/jobs/components/job-detail-panel.tsx", "utf8");
  assert.match(source, /输出结果/);
  assert.match(source, /<JobResultImage/);
  assert.match(source, /<JobDeliverablesDownload/);
  assert.match(source, /输入素材不会被当作生成结果/);
  assert.match(source, /safeJobFailureSummary/);
  assert.doesNotMatch(source, /error_details/);
  assert.doesNotMatch(source, /error_message/);
});
