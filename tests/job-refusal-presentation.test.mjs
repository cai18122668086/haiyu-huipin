import assert from "node:assert/strict";
import test from "node:test";
import { jobNote, safeJobFailureSummary } from "../lib/jobs/presentation.ts";

test("unclear photos request the right replacement while inspection outages stay retryable", () => {
  for (const [code, advice] of [["SOURCE_PRODUCT_UNCLEAR", /完整轮廓/], ["SOURCE_PRINT_UNREADABLE", /对焦印字/], ["SOURCE_DETAIL_UNCLEAR", /细节近照/]]) {
    const message = safeJobFailureSummary(code);
    assert.match(message, advice);
    assert.match(message, /上传/);
    assert.equal(jobNote({ status: "partial", items: [{ status: "failed", error_code: code }] }), message);
  }
  assert.doesNotMatch(safeJobFailureSummary("IMAGE_FIDELITY_PREPARATION_FAILED"), /重新上传/);
});

test("content refusal has an actionable safe summary for new and historical jobs", () => {
  const summary = safeJobFailureSummary("IMAGE_CONTENT_REJECTED");
  assert.match(summary, /内容限制/);
  assert.doesNotMatch(summary, /稍后重试|服务暂时不可用/);
  assert.equal(safeJobFailureSummary("IMAGE_REQUEST_FAILED", {
    provider_error: { code: "content_policy_violation", message: "sk-private-upstream-receipt" },
  }), summary);
  assert.equal(jobNote({ status: "failed", items: [{ status: "failed", error_code: "IMAGE_REQUEST_FAILED", error_details: { provider_error: { code: "content_policy_violation" } } }] }), summary);
  assert.doesNotMatch(summary, /sk-private|provider/);
});

test("a rejected source inspection directs the user to support with the job id", () => {
  const code = "IMAGE_FIDELITY_PREPARATION_FAILED";
  const details = { image_fidelity: { preparation_stage: "source_selection", planner_http_status: 400 } };
  const note = jobNote({ status: "failed", items: [{ status: "failed", error_code: code, error_details: details }] });
  assert.match(note, /联系客服并提供任务编号/);
  assert.doesNotMatch(note, /稍后重试|重新上传/);
  assert.match(safeJobFailureSummary(code), /稍后重试/);
});

test("technical rejection and unknown outcome keep their distinct recovery messages", () => {
  assert.match(safeJobFailureSummary("IMAGE_REQUEST_FAILED", { provider_error: { code: "invalid_image" } }), /暂时不可用/);
  assert.match(safeJobFailureSummary("PROVIDER_OUTCOME_UNKNOWN"), /结果尚未确认/);
  assert.match(safeJobFailureSummary("PROVIDER_OUTCOME_UNKNOWN", { provider_error: { code: "content_policy_violation" } }), /结果尚未确认/);
});

test("job-level reconciliation asks for the local task ID without exposing supplier metadata", () => {
  const summary = jobNote({ status: "failed", items: [], error_code: "PROVIDER_OUTCOME_UNKNOWN", error_details: { image_recovery: { original_task_id: "private-supplier-receipt" } } });
  assert.match(summary, /本站任务编号.*核查原任务/);
  assert.doesNotMatch(summary, /private-supplier-receipt|稍后重试/);
});

test("failed original async tasks show the upstream failure without inviting a duplicate", () => {
  const summary = safeJobFailureSummary("IMAGE_ASYNC_TASK_FAILED");
  assert.match(summary, /生成服务报告原任务异常/);
  assert.match(summary, /核查原任务/);
  assert.doesNotMatch(summary, /结果尚未确认|稍后重试/);
  assert.equal(jobNote({ status: "failed", items: [{ status: "failed", error_code: "IMAGE_ASYNC_TASK_FAILED" }] }), summary);
});

test("partial results with a held provider task direct users to reconciliation", () => {
  const note = jobNote({ status: "partial", allowed_actions: [], items: [
    { status: "succeeded" }, { status: "failed", error_code: "PROVIDER_OUTCOME_UNKNOWN" },
  ] });
  assert.match(note, /部分结果已完成.*核查原任务/);
  assert.doesNotMatch(note, /重试失败项目|重新上传/);
  assert.doesNotMatch(jobNote({ status: "partial", allowed_actions: [] }), /重试/);
  assert.match(jobNote({ status: "partial", allowed_actions: ["retry"] }), /重试失败项目/);
});

test("a reference download timeout keeps its specific recovery message on partial jobs", () => {
  const item = { status: "failed", error_code: "PROVIDER_OUTCOME_UNKNOWN",
    error_details: { provider_error: { code: "image_reference_fetch_timeout", message: "private supplier trace" } } };
  const note = jobNote({ status: "partial", items: [item] });
  assert.match(note, /部分结果已完成.*下载参考图超时.*核查原任务/);
  assert.doesNotMatch(note, /private supplier trace|稍后重试/);
});
