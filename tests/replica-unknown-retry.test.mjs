import assert from "node:assert/strict";
import test from "node:test";
import { replicaRetryNeedsReconciliation } from "../lib/replica/progress.ts";

test("unknown original image requests are reconciled rather than cloned into retries", () => {
  assert.equal(replicaRetryNeedsReconciliation({error_code:"PROVIDER_OUTCOME_UNKNOWN"}), true);
  assert.equal(replicaRetryNeedsReconciliation({items:[{error_code:"IMAGE_RESULT_DOWNLOAD_UNCONFIRMED"}]}), true);
  assert.equal(replicaRetryNeedsReconciliation({error_code:"REPLICA_CANVAS_MISMATCH"}), false);
  assert.equal(replicaRetryNeedsReconciliation(undefined), false);
});
