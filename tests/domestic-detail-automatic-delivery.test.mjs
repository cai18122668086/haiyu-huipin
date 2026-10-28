import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const require = createRequire(import.meta.url);
function load(file, resolver = require) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(file, import.meta.url), "utf8"), { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  const testModule = { exports: {} }; new Function("require", "module", "exports", outputText)(resolver, testModule, testModule.exports); return testModule.exports;
}
const idempotency = load("../lib/api/idempotency.ts");
const keys = load("../features/jobs/api/jobs-keys.ts");
function harness() {
  const calls = []; let unknown = false;
  const resolver = name => {
    if (name === "react") return { useRef: () => ({ current: null }) };
    if (name === "@tanstack/react-query") return { useQuery: options => options, useQueryClient: () => ({ invalidateQueries: async () => {} }), useMutation: options => ({ mutateAsync: async input => { const value = await options.mutationFn(input); await options.onSuccess?.(value, input); return value; } }) };
    if (name === "@/lib/api/idempotency") return idempotency;
    if (name === "@/lib/api/client") return { apiClient: { GET: async (url, request) => { calls.push({ url, request }); return { data: {} }; }, POST: async (url, request) => { calls.push({ url, request }); if (unknown) { unknown = false; throw new Error("unknown HTTP outcome"); } return { data: {} }; } } };
    if (name === "@/lib/api/request") return { unwrapApiResponse: result => result.data };
    if (name === "@/lib/api/types") return { isActiveJob: status => ["queued", "planning", "processing", "cancelling"].includes(status) };
    if (name === "@/lib/jobs/actions") return { assertJobAction: (allowed, action) => assert.ok(allowed.includes(action)) };
    if (name === "@/lib/api/error-mapper") return {};
    if (name === "./jobs-keys") return keys;
    if (name === "@/lib/api/cursor-pages") return load("../lib/api/cursor-pages.ts");
    return require(name);
  };
  return { jobs: load("../features/jobs/api/jobs.ts", resolver), automatic: load("../features/jobs/api/automatic-delivery.ts", resolver), calls, lose: () => { unknown = true; } };
}
const quote = (override = {}) => ({ job_id: "source-a", workspace_id: "ws-a", plan_revision: 3, delivery_mode: "AUTO_FINALIZE_AUTHORIZED", sha256: "a".repeat(64), authorization_text: "Explicit current automatic delivery authorization", completion_disclosure: "Final packing adds zero calls; residuals remain disclosed.", ...override });

test("ordinary confirmation keeps its original body and omits every auto field", async () => {
  const h = harness(); await h.jobs.useConfirmPlan("ws-a").mutateAsync({ jobId: "source-a", revision: 3, allowedActions: ["confirm_plan"], approvalText: "ordinary plan approval" });
  const body = h.calls[0].request.body;
  assert.equal(body.approval_text, "ordinary plan approval");
  for (const key of ["domestic_detail_delivery_mode", "automatic_delivery_authorization_text", "automatic_delivery_authorization_sha256"]) assert.equal(Object.hasOwn(body, key), false);
});

test("explicit auto consent is submitted verbatim and keeps its key after unknown HTTP", async () => {
  const h = harness(); const confirm = h.jobs.useConfirmPlan("ws-a");
  const value = { jobId: "source-a", revision: 3, allowedActions: ["confirm_plan"], approvalText: "human base approval", automaticDeliveryAuthorization: quote() };
  h.lose(); await assert.rejects(confirm.mutateAsync(value));
  await confirm.mutateAsync({ ...value, automaticDeliveryAuthorization: quote({ displayOnly: "ignored in HTTP intent" }) });
  assert.equal(h.calls[0].request.params.header["Idempotency-Key"], h.calls[1].request.params.header["Idempotency-Key"]);
  assert.equal(h.calls[1].request.body.domestic_detail_delivery_mode, "AUTO_FINALIZE_AUTHORIZED");
  assert.equal(h.calls[1].request.body.automatic_delivery_authorization_text, value.automaticDeliveryAuthorization.authorization_text);
  assert.equal(h.calls[1].request.body.automatic_delivery_authorization_sha256, value.automaticDeliveryAuthorization.sha256);
  await confirm.mutateAsync({ ...value, automaticDeliveryAuthorization: quote({ sha256: "b".repeat(64) }) });
  assert.notEqual(h.calls[1].request.params.header["Idempotency-Key"], h.calls[2].request.params.header["Idempotency-Key"]);
});

test("authorization quote is opt-in and source/revision scoped", async () => {
  const h = harness(); const job = { id: "source-a", status: "awaiting_confirmation", configuration: { domestic_detail_pipeline: "aurora-domestic-detail-v4.3.1" }, plan: { revision: 3, payload: { native_approval: { document_sha256: "a".repeat(64) } } } };
  assert.equal(h.automatic.useAutomaticDeliveryAuthorization("ws-a", job, false).enabled, false);
  const requested = h.automatic.useAutomaticDeliveryAuthorization("ws-a", job, true); assert.equal(requested.enabled, true);
  await requested.queryFn({ signal: undefined }); assert.equal(h.calls[0].request.params.path.job_id, "source-a");
  assert.notDeepEqual(requested.queryKey, h.automatic.useAutomaticDeliveryAuthorization("ws-a", { ...job, plan: { ...job.plan, revision: 4 } }, true).queryKey);
  assert.equal(h.automatic.useAutomaticDeliveryAuthorization("ws-a", { ...job, status: "processing" }, true).enabled, false);
});

test("recovery reuses its original source authorization and unknown-HTTP key", async () => {
  const h = harness(); const recover = h.automatic.useRecoverAutomaticDelivery("ws-a"); const value = { jobId: "source-a", authorizationSha256: "a".repeat(64) };
  h.lose(); await assert.rejects(recover.mutateAsync(value)); await recover.mutateAsync(value);
  assert.equal(h.calls[0].request.params.header["Idempotency-Key"], h.calls[1].request.params.header["Idempotency-Key"]);
  assert.deepEqual(h.calls[1].request.body, { authorization_sha256: value.authorizationSha256 });
  assert.ok(h.calls[1].url.endsWith("/automatic-delivery/recover"));
});

test("automatic polling stops on required reconciliation and never activates manual tasks", () => {
  const h = harness(); const poll = h.jobs.useJob("ws-a", "source-a").refetchInterval;
  const data = { status: "awaiting_final_review", stage: "automatic_delivery_reconciliation_requested", configuration: { domestic_detail_pipeline: "aurora-domestic-detail-v4.3.1" }, plan: { payload: { aurora_v4_3: { approval_receipt: { automatic_delivery_authorization: quote() } } } } };
  assert.equal(poll({ state: { data } }), 1500);
  assert.equal(poll({ state: { data: { ...data, stage: "automatic_delivery_reconciliation_required" } } }), false);
  assert.equal(poll({ state: { data: { ...data, plan: undefined } } }), false);
  assert.equal(poll({ state: { data: { ...data, configuration: {} } } }), false);
});

const automaticUi = load("../components/domestic-detail-automatic-delivery.tsx", name => name === "@/features/jobs/api/automatic-delivery" || name === "@/lib/api/request" ? {} : require(name));
test("automatic delivery starts off and consent never follows a stale quote", () => {
  const render = props => renderToStaticMarkup(createElement(automaticUi.AutomaticDeliveryChoice, { requested: false, busy: false, onChange: () => {}, ...props }));
  const ordinary = render({}); assert.ok(!ordinary.includes('checked=""')); assert.ok(ordinary.includes("默认在生成完成后等待你审阅结果"));
  const pending = render({ requested: true, quote: quote(), selected: quote({ sha256: "b".repeat(64) }) });
  assert.equal((pending.match(/checked=""/g) ?? []).length, 1); assert.ok(pending.includes(quote().authorization_text));
});

const approval = load("../components/domestic-detail-approval.tsx");
const finalUi = load("../components/domestic-detail-final-review.tsx", name => {
  if (name === "@/components/domestic-detail-approval") return approval;
  if (name === "@/components/job-result-image") return { JobResultImage: props => createElement("figure", { "data-asset": props.assetId }, props.label) };
  return require(name);
});
test("an automatic record never renders a post-generation human-confirmation checkbox", () => {
  const review = { job_id: "source-a", quality_state: "WITH_RESIDUALS", delivery_mode: "AUTO_FINALIZE_AUTHORIZED", sha256: "f".repeat(64), document_markdown: "original residuals remain", pair_canvases: [] };
  const html = renderToStaticMarkup(createElement(finalUi.DomesticDetailFinalReview, { review, reviewed: false, onReviewed: () => {}, completed: true }));
  assert.ok(html.includes("已按授权完成自动交付")); assert.ok(html.includes("不表示你已逐屏审阅最终成图")); assert.ok(!html.includes('type="checkbox"'));
});
