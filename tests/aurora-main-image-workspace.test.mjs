import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (file) => readFile(file, "utf8");

test("Aurora domestic main image keeps the six-page Harness workflow visible", async () => {
  const [page, workspace, registry, navigation, jobs, sidebar, drawer, workspaceLink, workspaceLinkResolution] = await Promise.all([
    read("app/workspace/domestic-main-image/page.tsx"),
    read("components/domestic-main-image-workspace.tsx"),
    read("lib/feature-registry.ts"),
    read("lib/navigation-registry.ts"),
    read("app/jobs/page.tsx"),
    read("components/tool-sidebar.tsx"),
    read("components/task-drawer.tsx"),
    read("lib/jobs/workspace-link.ts"),
    read("lib/jobs/workspace-link-resolution.ts"),
  ]);

  assert.match(page, /DomesticMainImageWorkspace/);
  assert.match(workspace, /type: "domestic_main_image"/);
  assert.match(workspace, /product_asset_ids/);
  assert.match(workspace, /onDrop={handleDrop}/);
  assert.match(workspace, /type="file" multiple/);
  assert.match(workspace, /把多张商品原图拖到这里/);
  assert.match(workspace, /MAX_SOURCE_IMAGES = 8/);
  assert.match(workspace, /duplicateCount/);
  assert.match(workspace, /确认方案并开始/);
  assert.match(workspace, /providerReady/);
  assert.match(workspace, /真实生成服务未启用/);
  assert.match(workspace, /没有生成真实图片/);
  assert.match(workspace, /results\.length === 6/);
  assert.match(workspace, /acceptedResultCount/);
  assert.match(workspace, /job\.data\?\.status === "completed"/);
  assert.match(workspace, /张已通过审核/);
  assert.match(workspace, /张图片已生成/);
  assert.match(workspace, /六张图片已生成，但整组审核未通过/);
  assert.match(workspace, /set-audit-failed/);
  assert.doesNotMatch(workspace, /DevelopmentProviderSettings/);
  assert.match(workspace, /按原方案重新生成/);
  assert.match(workspace, /resultSource === "generated" \|\| resultSource === "reused"/);
  assert.match(workspace, /useRetryJob/);
  assert.match(workspace, /scope: "failed"/);
  assert.doesNotMatch(workspace, /useCloneJob/);
  assert.match(workspace, /"completed", "failed", "partial", "cancelled"/);
  assert.match(workspace, /任务已经终止，不再继续运行/);
  assert.match(workspace, /被前页阻断/);
  assert.match(workspace, /图片服务返回了/);
  assert.match(workspace, /生成主图六件套/);
  assert.match(registry, /aurora-domestic-ecommerce-main-image/);
  assert.match(registry, /aurora_six_page_reviewed_set/);
  assert.match(navigation, /navigationFeature\("domestic-main-image"/);
  assert.match(workspace, /searchParams\.get\("jobId"\)/);
  assert.match(workspace, /router\.replace\(`\/workspace\/domestic-main-image\?jobId=/);
  assert.match(workspace, /restoredAssetIds/);
  assert.match(jobs, /jobWorkspaceHref\(job\)/);
  assert.match(sidebar, /jobWorkspaceHref\(job\)/);
  assert.match(drawer, /jobWorkspaceHref\(job\)/);
  assert.match(drawer, /Mock 流程已结束，没有生成真实图片/);
  assert.match(workspaceLink, /resolveJobWorkspaceHref\(job, FEATURE_REGISTRY\)/);
  assert.match(workspaceLinkResolution, /feature\.route}\?jobId=/);
});
