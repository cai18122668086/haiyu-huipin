import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const utf8 = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");
const root = fileURLToPath(new URL("..", import.meta.url));

test("OpenAPI exposes every frontend integration operation", async () => {
  const document = JSON.parse(await utf8("docs/openapi-v1.json"));
  const expected = {
    "/api/v1/runtime": ["get"],
    "/api/v1/workspaces": ["get"],
    "/api/v1/workspaces/{workspace_id}/assets": ["get", "post"],
    "/api/v1/workspaces/{workspace_id}/assets/{asset_id}/access-url": ["post"],
    "/api/v1/workspaces/{workspace_id}/model-pose-transfer/pose-assets": ["post"],
    "/api/v1/workspaces/{workspace_id}/jobs": ["get", "post"],
    "/api/v1/workspaces/{workspace_id}/jobs/{job_id}": ["get"],
    "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/deliverables.zip": ["get"],
    "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/cancel": ["post"],
    "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/retry": ["post"],
    "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/plan/confirm": ["post"],
  };
  for (const [path, methods] of Object.entries(expected)) {
    assert.ok(document.paths[path], `${path} should exist`);
    for (const method of methods) {
      assert.ok(document.paths[path][method], `${method.toUpperCase()} ${path} should exist`);
    }
  }

  assert.ok(document.components.schemas.JobType.enum.includes("model_pose_transfer"));
  assert.ok(document.components.schemas.ModelPoseTransferJobCreate);
  const jobsSchema = document.paths["/api/v1/workspaces/{workspace_id}/jobs"]
    .post.requestBody.content["application/json"].schema;
  assert.ok(jobsSchema.oneOf.some(
    (entry) => entry.$ref === "#/components/schemas/ModelPoseTransferJobCreate",
  ));
  const materializeResponse = document.paths[
    "/api/v1/workspaces/{workspace_id}/model-pose-transfer/pose-assets"
  ].post.responses["201"].content["application/json"].schema;
  assert.equal(materializeResponse.$ref, "#/components/schemas/AssetResponse");
});

test("business UI is API-backed and runtime capability is fail-closed", async () => {
  const businessFiles = [
    "app/assets/page.tsx",
    "app/dashboard/page.tsx",
    "app/jobs/page.tsx",
    "app/workspace/detail-pack/page.tsx",
    "app/workspace/local-edit/page.tsx",
    "components/task-drawer.tsx",
    "components/tool-sidebar.tsx",
  ];
  const contents = await Promise.all(businessFiles.map(utf8));
  assert.equal(contents.some((source) => source.includes("mock-data")), false);
  const runtime = await utf8("features/runtime/api/runtime.ts");
  assert.match(runtime, /availability === "available"/);
  assert.match(runtime, /execution === "provider"/);
  assert.match(runtime, /canSubmit: false/);
  assert.match(runtime, /当前生成服务暂不可用/);
});

test("browser API client defaults to the same-origin edge proxy", async () => {
  const client = await utf8("lib/api/client.ts");
  assert.ok(client.includes(
    'process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || ""',
  ));
  assert.equal(client.includes("localhost:8000"), false);
});

test("development auth bypass is explicit and skips SuperTokens initialization", async () => {
  const config = await utf8("lib/auth/config.ts");
  const providers = await utf8("app/providers.tsx");
  const boundary = await utf8("components/auth/auth-boundary.tsx");

  assert.match(config, /NEXT_PUBLIC_AUTH_MODE === "development"/);
  assert.match(providers, /getAuthMode\(\) !== "development"/);
  assert.match(boundary, /if \(!developmentAuth\) initializeAuthClient\(\)/);
  assert.match(boundary, /if \(developmentAuth\) return routeRequiresWorkspace/);
});

test("runtime sources only allow loopback URLs and the approved customer-support link", async () => {
  const files = await sourceFiles(["app", "components", "features", "lib"]);
  for (const file of files) {
    const source = await readFile(file, "utf8");
    for (const match of source.matchAll(/https?:\/\/[^\s"'`<>]+/g)) {
      const url = new URL(match[0]);
      const relative = path.relative(root, file).split(path.sep).join("/");
      const approvedSupportLink = [
        "app/help/page.tsx", "app/legal/privacy/page.tsx", "app/legal/terms/page.tsx",
      ].includes(relative) && url.href === "https://work.weixin.qq.com/kfid/kfc1f8fc41acaca0895";
      assert.ok(
        ["localhost", "127.0.0.1", "app.local"].includes(url.hostname) || approvedSupportLink,
        `${path.relative(root, file)} contains remote runtime URL ${url.origin}`,
      );
    }
  }
});

test("public service copy stays honest and icon actions have stable names", async () => {
  const detail = await utf8("app/workspace/detail-pack/page.tsx");
  const local = await utf8("app/workspace/local-edit/page.tsx");
  const helpPanel = await utf8("components/help-panel.tsx");
  const jobs = await utf8("app/jobs/page.tsx");

  assert.match(detail, /任务已完成 · 暂无图片结果/);
  assert.match(detail, /服务状态确认中 · 生成服务暂不可用/);
  assert.match(detail, /生成服务状态确认中，确认入口暂时关闭/);
  assert.match(local, /当前为流程预览，暂不生成图片/);
  assert.match(local, /服务状态确认中，暂不可创建/);
  assert.match(helpPanel, /当前可预览操作流程，暂不生成图片/);
  assert.match(helpPanel, /生成服务暂不可用/);
  for (const source of [detail, local, helpPanel]) {
    assert.doesNotMatch(source, /模拟流程|能力未知|真实生成未启用|Provider 交付/);
  }
  assert.match(
    await utf8("components/task-drawer.tsx"),
    /inert=\{open \? undefined : true\}/,
    "closed drawer controls must not remain focusable",
  );
  assert.doesNotMatch(
    jobs,
    /<button(?=[^>]*\btitle=)(?![^>]*\baria-label=)[^>]*>/,
    "title-only job action buttons are not accessible names",
  );
});

test("write actions use server allowed_actions as the only authority", async () => {
  const jobsPage = await utf8("app/jobs/page.tsx");
  const drawer = await utf8("components/task-drawer.tsx");
  const detail = await utf8("app/workspace/detail-pack/page.tsx");
  const local = await utf8("app/workspace/local-edit/page.tsx");
  const mutations = await utf8("features/jobs/api/jobs.ts");

  for (const source of [jobsPage, drawer, detail, local]) {
    assert.match(source, /allowsJobAction\(/);
  }
  for (const action of ["cancel", "retry", "confirm_plan"]) {
    assert.match(
      mutations,
      new RegExp(`assertJobAction\\(input\\.allowedActions, "${action}"\\)`),
    );
  }
});

test("workflow pages submit the bounded inputs and failed-only retry scope", async () => {
  const detail = await utf8("app/workspace/detail-pack/page.tsx");
  const local = await utf8("app/workspace/local-edit/page.tsx");

  assert.match(local, /mediaKind: "mask"/);
  assert.match(
    local,
    /input: \{ source_asset_id: sourceAsset\.id, selection_mask_asset_id: mask\.id \}/,
  );
  assert.match(
    local,
    /configuration: \{ instruction: prompt\.trim\(\), output_format: "png", quality: "balanced" \}/,
  );
  assert.match(
    detail,
    /input: \{ product_groups: \[\{ group_key: "primary-product", asset_ids: productSources\.map/,
  );
  for (const source of [detail, local]) {
    assert.match(source, /scope: "failed"/);
  }
});


test("upload inputs match the backend image contract and recover from file-level failures", async () => {
  const assets = await utf8("app/assets/page.tsx");
  const detail = await utf8("app/workspace/detail-pack/page.tsx");
  const commerce = await utf8("components/commerce-skill-workspace.tsx");
  const product = await utf8("components/product-image-workspace.tsx");

  const localEdit = await utf8("app/workspace/local-edit/page.tsx");
  const uploadContract = await utf8("lib/image-upload.ts");

  assert.match(uploadContract, /IMAGE_UPLOAD_EXTENSIONS = \["\.jpg", "\.jpeg", "\.png", "\.webp"\]/);
  assert.match(uploadContract, /"image\/jpeg",\s*"image\/png",\s*"image\/webp"/);
  const dropZone = await utf8("components/image-drop-zone.tsx");
  assert.match(dropZone, /accept=\{IMAGE_UPLOAD_ACCEPT\}/);
  for (const source of [assets, detail, commerce, localEdit, product]) assert.match(source, /<ImageDropZone/);
  for (const source of [assets, detail, localEdit, commerce, product, dropZone]) assert.doesNotMatch(source, /accept="image\/\*"/);
  assert.match(dropZone, /event\.currentTarget\.value = ""/);
  assert.match(assets, /await upload\.mutateAsync\(\{ file \}\)/);
  assert.match(detail, /const failures: string\[\] = \[\]/);
  assert.match(detail, /setUploadError\(/);
  for (const source of [commerce, product]) {
    assert.match(source, /const failures: string\[\] = \[\]/);
    assert.match(source, /if \(uploadedIds\.length\)/);
    assert.match(source, /failures\.push\(/);
  }
});
test("mask editor exposes brush, eraser, and rectangle authority tools", async () => {
  const editor = await utf8("components/mask-editor.tsx");
  assert.match(editor, /"brush" \| "eraser" \| "rectangle"/);
  assert.match(editor, /tool === "rectangle"/);
  assert.match(editor, /context\.fillRect\(left, top, width, height\)/);
  assert.match(editor, /aria-label="矩形框选"/);
  assert.match(editor, /disabled=\{!ready \|\| tool === "rectangle"\}/);
});

async function sourceFiles(directories) {
  const results = [];
  for (const directory of directories) {
    await walk(path.join(root, directory), results);
  }
  return results;
}

async function walk(directory, results) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "generated") continue;
      await walk(target, results);
    } else if (/\.(?:ts|tsx|js|jsx|css)$/.test(entry.name)) {
      results.push(target);
    }
  }
}
