import assert from "node:assert/strict";
import test from "node:test";
import { jobNote, safeJobFailureSummary } from "../lib/jobs/presentation.ts";

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

test("technical rejection and unknown outcome keep their distinct recovery messages", () => {
  assert.match(safeJobFailureSummary("IMAGE_REQUEST_FAILED", { provider_error: { code: "invalid_image" } }), /暂时不可用/);
  assert.match(safeJobFailureSummary("PROVIDER_OUTCOME_UNKNOWN"), /结果尚未确认/);
  assert.match(safeJobFailureSummary("PROVIDER_OUTCOME_UNKNOWN", { provider_error: { code: "content_policy_violation" } }), /结果尚未确认/);
});
