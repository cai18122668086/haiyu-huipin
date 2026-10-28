import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { FEATURE_REGISTRY, PRODUCT_IMAGE_FEATURES } from "../lib/feature-registry.ts";
import { resolveJobWorkspaceHref } from "../lib/jobs/workspace-link-resolution.ts";

test("product-image list jobs route by their explicit task preset", () => {
  for (const feature of PRODUCT_IMAGE_FEATURES) {
    assert.equal(
      resolveJobWorkspaceHref(
        { id: `job-${feature.taskPreset}`, type: "product_image", task_preset: feature.taskPreset },
        FEATURE_REGISTRY,
      ),
      `${feature.route}?jobId=job-${feature.taskPreset}`,
    );
  }
});

test("scene-main detail jobs route from configuration.settings.task_type", () => {
  assert.equal(
    resolveJobWorkspaceHref(
      {
        id: "job scene/main",
        type: "product_image",
        configuration: { settings: { task_type: "scene_main" } },
      },
      FEATURE_REGISTRY,
    ),
    "/workspace/product-image/scene-main?jobId=job%20scene%2Fmain",
  );
});

test("missing or unknown product-image presets fall back to job details", () => {
  for (const job of [
    { id: "missing", type: "product_image" },
    { id: "unknown", type: "product_image", task_preset: "not_a_real_preset" },
  ]) {
    assert.equal(resolveJobWorkspaceHref(job, FEATURE_REGISTRY), `/jobs?jobId=${job.id}`);
  }
});

test("the jobs page opens a fallback jobId in the detail panel", async () => {
  const jobsPage = await readFile("app/jobs/page.tsx", "utf8");

  assert.match(jobsPage, /searchParams\.get\("jobId"\)/);
  assert.match(jobsPage, /activeJobId = requestedJobId/);
  assert.doesNotMatch(jobsPage, /selectedJobId/);
  assert.match(jobsPage, /jobId={activeJobId}/);
});
