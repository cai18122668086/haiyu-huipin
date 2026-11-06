import assert from "node:assert/strict";
import test from "node:test";
import { replicaFreeRestartAvailable, replicaRetryNeedsReconciliation } from "../lib/replica/progress.ts";

test("unknown original image requests are reconciled rather than cloned into retries", () => {
  assert.equal(replicaRetryNeedsReconciliation({error_code:"PROVIDER_OUTCOME_UNKNOWN"}), true);
  assert.equal(replicaRetryNeedsReconciliation({items:[{error_code:"IMAGE_RESULT_DOWNLOAD_UNCONFIRMED"}]}), true);
  assert.equal(replicaRetryNeedsReconciliation({error_code:"IMAGE_ASYNC_TASK_FAILED"}), true);
  assert.equal(replicaRetryNeedsReconciliation({items:[{error_code:"IMAGE_ASYNC_TASK_FAILED"}]}), true);
  assert.equal(replicaRetryNeedsReconciliation({error_code:"REPLICA_CANVAS_MISMATCH"}), false);
  assert.equal(replicaRetryNeedsReconciliation(undefined), false);
});

test("zero-delivery free trials offer an explicit restart while paid and active jobs remain fenced", () => {
  const failed = { status: "failed", succeeded_item_count: 0, assets: [] };
  assert.equal(replicaFreeRestartAvailable(failed, true), true);
  assert.equal(replicaFreeRestartAvailable(failed, false), false);
  assert.equal(replicaFreeRestartAvailable({ ...failed, status: "processing" }, true), false);
  assert.equal(replicaFreeRestartAvailable({ ...failed, succeeded_item_count: 1 }, true), false);
  assert.equal(replicaFreeRestartAvailable({ ...failed, assets: [{ direction: "output", is_deliverable: true }] }, true), false);
  assert.equal(replicaFreeRestartAvailable(undefined, true), false);
});
