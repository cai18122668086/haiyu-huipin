import assert from "node:assert/strict";
import test from "node:test";
import { jobNote, safeJobFailureSummary } from "../lib/jobs/presentation.ts";

test("saved edit candidates report review failure and the recovery action", () => {
  const note = safeJobFailureSummary("LOCAL_EDIT_VISUAL_REVIEW_REQUEST_FAILED");
  assert.match(note, /图片已生成/);
  assert.match(note, /继续审核已保存的图片/);
  assert.doesNotMatch(
    safeJobFailureSummary("LOCAL_EDIT_VISUAL_REVIEW_REJECTED"),
    /服务暂时不可用/,
  );
});

test("failed job uses its item error when the job-level error is absent", () => {
  const note = jobNote({
    status: "failed",
    error_code: null,
    items: [{ status: "failed", error_code: "LOCAL_EDIT_VISUAL_REVIEW_REQUEST_FAILED" }],
  });
  assert.match(note, /图片已生成/);
});

test("unconfirmed image requests do not encourage automatic regeneration", () => {
  assert.match(safeJobFailureSummary("PROVIDER_OUTCOME_UNKNOWN"), /已停止自动重试/);
});
