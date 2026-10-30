import assert from "node:assert/strict";
import test from "node:test";
import { mainImageRecoveryMode } from "../lib/jobs/main-image-recovery.ts";

const failed = { status: "failed", stage: "planning_failed", allowed_actions: ["retry"] };
const plan = { confirmed_at: "2026-10-30T01:43:00Z", payload: { pages: Array(6).fill({}) } };

test("planning failure offers replanning while complete confirmed plans offer reuse", () => {
  assert.equal(mainImageRecoveryMode(failed), "replan");
  assert.equal(mainImageRecoveryMode({ ...failed, plan }), "retry");
  assert.equal(mainImageRecoveryMode({ ...failed, status: "partial", plan }), "retry");
});

test("unconfirmed or incomplete plans and unauthorized actions cannot promise reuse", () => {
  for (const job of [
    undefined,
    { ...failed, allowed_actions: [] },
    { ...failed, status: "processing", plan },
    { ...failed, stage: "generating" },
    { ...failed, plan: { ...plan, confirmed_at: null } },
    { ...failed, plan: { ...plan, payload: { pages: [] } } },
  ]) assert.equal(mainImageRecoveryMode(job), null);
});
