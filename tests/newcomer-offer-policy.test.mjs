import assert from "node:assert/strict";
import test from "node:test";
import { newcomerReminderReady, newcomerDialogBlocks, newcomerResultSource } from "../lib/newcomer-offer-policy.ts";

const state = { eligible: true, trial_viewed: true, reminded_today: false, server_now: "2026-11-07T00:00:00Z", ready_at: "2026-11-07T00:00:30Z" };
test("first result is ready at thirty seconds without waiting for the next poll", () => {
  assert.equal(newcomerReminderReady(state, 29_999), false);
  assert.equal(newcomerReminderReady(state, 30_000), true);
  assert.equal(newcomerReminderReady({...state, ready_at: "2026-11-07T00:00:05Z"}, 5000), true);
});
test("a closed or cross-tab claimed reminder stays suppressed and missing results never trigger", () => {
  for (const patch of [{ eligible: false }, { trial_viewed: false }, { reminded_today: true }, { ready_at: null }, { ready_at: "invalid" }]) {
    assert.equal(newcomerReminderReady({...state, ...patch}, 120_000), false);
  }
});
test("task details permit the reminder while image, login and payment dialogs block", () => {
  const detail = { matches: selector => selector === '[data-newcomer-result-detail="true"]', closest: () => null };
  assert.equal(newcomerDialogBlocks(detail), false);
  assert.equal(newcomerDialogBlocks({matches: () => false, closest: () => null}), true);
  assert.equal(newcomerDialogBlocks({matches: () => false, closest: () => ({})}), false);
});
test("generation history uses the grouped replica session while task details use the job", () => {
  assert.deepEqual(newcomerResultSource("rep_history"), { session_id: "rep_history" });
  assert.deepEqual(newcomerResultSource("job_detail"), { job_id: "job_detail" });
});
