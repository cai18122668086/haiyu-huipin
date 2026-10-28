import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function load(file, customRequire = require) {
  const source = readFileSync(new URL(file, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } });
  const testModule = { exports: {} };
  new Function("require", "module", "exports", outputText)(customRequire, testModule, testModule.exports);
  return testModule.exports;
}
const approval = load("../components/domestic-detail-approval.tsx");
const { DomesticDetailFinalReview, isCurrentFinalReview } = load("../components/domestic-detail-final-review.tsx", name => {
  if (name === "@/components/domestic-detail-approval") return approval;
  if (name === "@/components/job-result-image") return { JobResultImage: props => createElement("figure", { "data-asset-id": props.assetId }, props.label) };
  return require(name);
});
function packet() {
  return {
    version: "domestic-detail-final-review-v1", job_id: "job-current", plan_id: "plan-current", plan_revision: 3,
    sha256: "a".repeat(64), skill_version: "4.3.1", context_sha256: "b".repeat(64), generation_binding_sha256: "c".repeat(64),
    quality_state: "WITH_RESIDUALS", campaign_audit_status: "NOT_REQUESTED",
    document_markdown: "# 最终审阅\n\n整页节奏、组间接缝和长图审核未执行。\n\n## 仍需接受的残差\nM01：瓶口几何仍有差异\nM08：文字仍需人工核对\n<script>untrusted()</script>\n",
    residual_issues: [{ module_id: "M01", issues: ["瓶口几何仍有差异"] }, { module_id: "M08", issues: ["文字仍需人工核对"] }],
    screens: Array.from({ length: 8 }, (_, i) => ({ asset_id: `screen-${i}`, module_id: `M${i + 1}` })),
    pair_canvases: Array.from({ length: 4 }, (_, i) => ({ asset_id: `master-${i}`, pair_id: `P${i + 1}` })),
  };
}
function render(review, extra = {}) {
  return renderToStaticMarkup(createElement(DomesticDetailFinalReview, { review, reviewed: false, onReviewed: () => {}, ...extra }));
}

test("final review retains every material residual and says which campaign review was not run", () => {
  const html = render(packet());
  for (const text of ["瓶口几何仍有差异", "文字仍需人工核对", "整页节奏、组间接缝和长图审核未执行", "存在未解决残差"]) assert.ok(html.includes(text));
  assert.ok(html.includes("&lt;script&gt;untrusted()&lt;/script&gt;"));
  assert.ok(!html.includes("<script>"));
  assert.equal((html.match(/data-asset-id="master-/g) ?? []).length, 4);
  assert.ok(!html.includes('checked=""'));
});

test("download contains exact UTF-8 review and original record including residuals", () => {
  const review = packet();
  const html = render(review);
  const markdown = html.match(/href="data:text\/markdown;charset=utf-8,([^"]*)"/);
  const json = html.match(/href="data:application\/json;charset=utf-8,([^"]*)"/);
  assert.equal(decodeURIComponent(markdown[1]), review.document_markdown);
  assert.deepEqual(JSON.parse(decodeURIComponent(json[1])), review);
});

test("another job, malformed fingerprint or incomplete/duplicate result cannot enable current review", () => {
  const review = packet();
  assert.equal(isCurrentFinalReview(review, "job-current", 8), true);
  assert.equal(isCurrentFinalReview(review, "job-other", 8), false);
  assert.equal(isCurrentFinalReview({ ...review, sha256: "unknown" }, "job-current", 8), false);
  assert.equal(isCurrentFinalReview(review, "job-current", 10), false);
  assert.equal(isCurrentFinalReview({ ...review, pair_canvases: [review.pair_canvases[0], ...review.pair_canvases.slice(0, 3)] }, "job-current", 8), false);
});

test("completed delivery shows the accepted scope without a new approval checkbox", () => {
  const html = render(packet(), { completed: true });
  assert.ok(html.includes("已确认的详情交付"));
  assert.ok(html.includes("文字仍需人工核对"));
  assert.ok(!html.includes('type="checkbox"'));
});


test("finalize hook keeps main-image request and intent unchanged, and scopes detail keys to reviewed bytes", async () => {
  const idempotency = load("../lib/api/idempotency.ts");
  const signatures = [];
  const calls = [];
  const { useFinalizeJob } = load("../features/jobs/api/jobs.ts", name => {
    if (name === "react") return { useRef: () => ({ current: null }) };
    if (name === "@tanstack/react-query") return {
      useMutation: options => ({ mutateAsync: options.mutationFn }),
      useQueryClient: () => ({ invalidateQueries: async () => {} }),
    };
    if (name === "@/lib/api/idempotency") return {
      ...idempotency, intentSignature: (operation, input) => { signatures.push(input); return idempotency.intentSignature(operation, input); },
    };
    if (name === "@/lib/api/client") return { apiClient: { POST: async (url, request) => { calls.push({ url, request }); return { data: {} }; } } };
    if (name === "@/lib/api/request") return { unwrapApiResponse: response => response.data };
    if (name === "@/lib/jobs/actions") return { assertJobAction: (allowed, action) => assert.ok(allowed.includes(action)) };
    if (name === "./jobs-keys") return { jobKeys: {} };
    if (name === "@/lib/api/cursor-pages") return load("../lib/api/cursor-pages.ts");
    if (["@/lib/api/types", "@/lib/api/error-mapper"].includes(name)) return {};
    return require(name);
  });
  const finalize = useFinalizeJob("ws-test");
  await finalize.mutateAsync({ jobId: "main-image", allowedActions: ["finalize"], approvalText: "original main approval" });
  assert.deepEqual(signatures[0], { jobId: "main-image", approvalText: "original main approval" });
  assert.deepEqual(calls[0].request.body, { approval_text: "original main approval" });
  const input = { jobId: "detail", allowedActions: ["finalize"], approvalText: "detail final review", finalReviewSha256: "a".repeat(64) };
  await finalize.mutateAsync(input);
  await finalize.mutateAsync({ ...input, finalReviewSha256: "b".repeat(64) });
  assert.equal(calls[1].request.body.final_review_sha256, input.finalReviewSha256);
  assert.notEqual(calls[1].request.params.header["Idempotency-Key"], calls[2].request.params.header["Idempotency-Key"]);
});
