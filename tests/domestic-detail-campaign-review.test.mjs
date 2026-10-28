import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const require = createRequire(import.meta.url);
function load(file, resolver = require) {
  const source = readFileSync(new URL(file, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  const testModule = { exports: {} }; new Function("require", "module", "exports", outputText)(resolver, testModule, testModule.exports); return testModule.exports;
}
const idempotency = load("../lib/api/idempotency.ts");
const keys = load("../features/jobs/api/jobs-keys.ts");

test("job history hook follows every cursor and keeps recent-only cache separate", async () => {
  const h = harness();
  h.result(request => request.params.query.cursor
    ? { items: [{ id: "job_old" }], has_more: false, next_cursor: null }
    : { items: [{ id: "job_new" }], has_more: true, next_cursor: "older", total: 2 });
  const full = h.jobs.useJobs("ws_a", { allPages: true });
  const recent = h.jobs.useJobs("ws_a", { limit: 20 });
  const result = await full.queryFn({ signal: undefined });
  assert.deepEqual(result.items.map(job => job.id), ["job_new", "job_old"]);
  assert.deepEqual(h.calls.map(call => call.request.params.query.cursor), [undefined, "older"]);
  assert.ok(h.calls.every(call => call.request.params.path.workspace_id === "ws_a"));
  assert.notDeepEqual(full.queryKey, recent.queryKey);
});
function harness() {
  const calls = []; let fail = false; let response = {};
  const client = {
    GET: async (url, request) => { calls.push({ url, request }); return { data: typeof response === "function" ? response(request) : response }; },
    POST: async (url, request) => { calls.push({ url, request }); if (fail) { fail = false; throw new Error("transport result unknown"); } return { data: { id: "review-created" } }; },
  };
  const resolver = name => {
    if (name === "react") return { useRef: () => ({ current: null }) };
    if (name === "@tanstack/react-query") return {
      useQuery: options => options, useInfiniteQuery: options => options,
      useQueryClient: () => ({ invalidateQueries: async () => {} }),
      useMutation: options => ({ mutateAsync: async input => { const value = await options.mutationFn(input); await options.onSuccess?.(value, input); return value; } }),
    };
    if (name === "@/lib/api/client") return { apiClient: client };
    if (name === "@/lib/api/idempotency") return idempotency;
    if (name === "@/lib/api/request") return { unwrapApiResponse: value => value.data };
    if (name === "./jobs-keys") return keys;
    if (name === "@/lib/api/cursor-pages") return load("../lib/api/cursor-pages.ts");
    if (["@/lib/api/types", "@/lib/api/error-mapper", "@/lib/jobs/actions"].includes(name)) return {};
    return require(name);
  };
  return { api: load("../features/jobs/api/campaign-reviews.ts", resolver), jobs: load("../features/jobs/api/jobs.ts", resolver), calls, unknown: () => { fail = true; }, result: value => { response = value; } };
}
const quote = (overrides = {}) => ({ source_job_id: "source-a", final_review_sha256: "f".repeat(64), sha256: "a".repeat(64), reserve_micros: 5000, model: "review-model", ...overrides });

test("paid review requires exact consent and retains the same HTTP key after transport unknown", async () => {
  const h = harness(); const mutation = h.api.useCreateCampaignReview("ws-a");
  const input = { sourceId: "source-a", quote: quote(), approvalText: h.api.CAMPAIGN_AUTHORIZATION };
  await assert.rejects(mutation.mutateAsync({ ...input, approvalText: "" })); assert.equal(h.calls.length, 0);
  await assert.rejects(mutation.mutateAsync({ ...input, sourceId: "source-other" })); assert.equal(h.calls.length, 0);
  h.unknown(); await assert.rejects(mutation.mutateAsync(input), /transport result unknown/);
  await mutation.mutateAsync(input);
  assert.equal(h.calls[0].request.params.header["Idempotency-Key"], h.calls[1].request.params.header["Idempotency-Key"]);
  assert.deepEqual(h.calls[0].request.body, { final_review_sha256: input.quote.final_review_sha256, quote_sha256: input.quote.sha256, authorized_reserve_micros: 5000, approval_text: input.approvalText });
  await mutation.mutateAsync({ ...input, quote: quote({ sha256: "b".repeat(64), reserve_micros: 6000 }) });
  assert.notEqual(h.calls[1].request.params.header["Idempotency-Key"], h.calls[2].request.params.header["Idempotency-Key"]);
});

test("saved recovery accepts only the original zero-model quote and preserves its retry key", async () => {
  const h = harness(); const mutation = h.api.useRecoverCampaignReview("ws-a");
  const value = { reviewId: "review-original", quote: { original_review_job_id: "review-original", model_requests: 0, image_requests: 0, reserve_micros: 0, original_result_journal_sha256: "c".repeat(64), sha256: "d".repeat(64) } };
  await assert.rejects(mutation.mutateAsync({ ...value, quote: { ...value.quote, model_requests: 1 } })); assert.equal(h.calls.length, 0);
  h.unknown(); await assert.rejects(mutation.mutateAsync(value)); await mutation.mutateAsync(value);
  assert.equal(h.calls[0].request.params.header["Idempotency-Key"], h.calls[1].request.params.header["Idempotency-Key"]);
  assert.deepEqual(h.calls[1].request.body, { original_result_journal_sha256: value.quote.original_result_journal_sha256, quote_sha256: value.quote.sha256 });
});

test("history uses the exact source endpoint and forwards its pagination cursor", async () => {
  const h = harness(); h.result({ items: [], next_cursor: "next-source-bound", has_more: true });
  const history = h.api.useCampaignHistory("ws-a", "source-a");
  const value = await history.queryFn({ pageParam: "earlier-page", signal: undefined });
  assert.equal(h.calls[0].request.params.path.job_id, "source-a");
  assert.ok(h.calls[0].url.endsWith("/campaign-reviews"));
  assert.equal(h.calls[0].request.params.query.cursor, "earlier-page");
  assert.equal(history.getNextPageParam(value), "next-source-bound");
  assert.notDeepEqual(history.queryKey, h.api.useCampaignHistory("ws-a", "source-b").queryKey);
});

test("a report from another source is rejected before displaying or selecting it", async () => {
  const h = harness(); h.result({ source_job_id: "source-other" });
  const report = h.api.useCampaignReport("ws-a", "source-a", { id: "review-exact", status: "failed" });
  assert.equal(report.enabled, true); await assert.rejects(report.queryFn({ signal: undefined }), /另一份详情/);
  assert.equal(h.calls[0].request.params.path.job_id, "review-exact");
});

test("final-review choice changes its query key and sends only the explicit ID and SHA", async () => {
  const h = harness(); const source = { id: "source-a", configuration: { domestic_detail_pipeline: "aurora-domestic-detail-v4.3.1" }, status: "awaiting_final_review", plan: { revision: 1 } };
  const base = h.jobs.useDomesticDetailFinalReview("ws-a", source);
  const choice = { review_job_id: "review-exact", report_sha256: "d".repeat(64) };
  const selected = h.jobs.useDomesticDetailFinalReview("ws-a", source, choice);
  assert.notDeepEqual(base.queryKey, selected.queryKey);
  await base.queryFn({ signal: undefined }); await selected.queryFn({ signal: undefined });
  assert.equal(Object.hasOwn(h.calls[0].request, "body"), false);
  assert.deepEqual(h.calls[1].request.body, choice);
});

const ui = load("../components/domestic-detail-campaign-review.tsx", name => {
  if (name === "@/features/jobs/api/campaign-reviews") return { CAMPAIGN_AUTHORIZATION: "explicit quoted authorization", useCreateCampaignReview: () => ({ isPending: false, error: undefined, mutateAsync: async () => { throw new Error("Render must not dispatch"); } }) };
  if (name === "@/features/jobs/api/jobs") return {};
  if (name === "@/components/status-badge") return {};
  if (name === "@/lib/api/request") return {};
  return require(name);
});

test("authorization renders unticked, with a disabled submit button and exact visible reserve", () => {
  const html = renderToStaticMarkup(createElement(ui.CampaignAuthorization, { workspaceId: "ws-a", sourceId: "source-a", quote: quote(), disabled: false, onOpen: () => {} }));
  assert.ok(html.includes('type="checkbox"')); assert.ok(!html.includes('checked=""'));
  assert.ok(html.includes('disabled=""')); assert.ok(html.includes("USD 0.005000"));
});

test("original report text is escaped and missing observations never render as an accepted audit", () => {
  const original = '<script>untrusted()</script>\n完整模型原文';
  const data = { report: { sha256: "a".repeat(64), native_gate_accepted: false, residual_checks: ["identity"] }, original_message_state: "PRESERVED_UTF8", original_message: original };
  const html = renderToStaticMarkup(createElement(ui.CampaignReportContent, { data, reviewId: "review-exact", canSelect: true, onSelect: () => {} }));
  assert.ok(html.includes("独立报告未被原生校验接受")); assert.ok(html.includes("identity"));
  assert.ok(html.includes("&lt;script&gt;")); assert.ok(!html.includes("<script>"));
  const raw = html.match(/href="data:text\/plain;charset=utf-8,([^"]*)"/); assert.equal(decodeURIComponent(raw[1]), original);
  const missing = renderToStaticMarkup(createElement(ui.CampaignReportContent, { data: { ...data, original_message_state: "UNAVAILABLE", original_message: null }, reviewId: "review-exact", canSelect: false, onSelect: () => {} }));
  assert.ok(missing.includes("没有可验证的原始模型回复，不视为审核通过"));
  assert.ok(!missing.includes("将此报告列入最终审阅"));
});
