import assert from "node:assert/strict";
import test from "node:test";

import {
  IdempotencyIntentRegistry,
  intentSignature,
  uploadIntentSignature,
} from "../lib/api/idempotency.ts";
import {
  allowsJobAction,
  assertJobAction,
} from "../lib/jobs/actions.ts";

const operationCases = [
  ["upload", { digest: "image-a", mediaKind: "image" }],
  ["create-job", { type: "detail_pack", assetIds: ["asset-a"] }],
  ["cancel-job", { jobId: "job-a" }],
  ["retry-job", { jobId: "job-a", scope: "failed", itemIds: [] }],
  ["confirm-plan", { jobId: "job-a", revision: 3 }],
];

for (const [operation, input] of operationCases) {
  test(`${operation} reuses its logical-intent key after a lost response`, () => {
    let keySequence = 0;
    let resourceSequence = 0;
    const registry = new IdempotencyIntentRegistry(
      () => `intent-key-${++keySequence}`,
    );
    const accepted = new Map();
    const signature = intentSignature(operation, input);

    const firstKey = registry.keyFor(signature);
    const firstResource = `resource-${++resourceSequence}`;
    accepted.set(firstKey, firstResource);
    // The server accepted the operation, but the client never received it.

    const retryKey = registry.keyFor(signature);
    const recoveredResource =
      accepted.get(retryKey) ?? `resource-${++resourceSequence}`;
    assert.equal(retryKey, firstKey);
    assert.equal(recoveredResource, firstResource);
    assert.equal(resourceSequence, 1, "the server resource must not duplicate");

    registry.complete(signature);
    assert.notEqual(
      registry.keyFor(signature),
      firstKey,
      "a confirmed success starts a fresh logical operation",
    );
  });
}

test("changed or abandoned input rotates the idempotency key", () => {
  let sequence = 0;
  const registry = new IdempotencyIntentRegistry(() => `key-${++sequence}`);
  const first = intentSignature("create-job", { prompt: "first" });
  const changed = intentSignature("create-job", { prompt: "changed" });
  assert.notEqual(registry.keyFor(first), registry.keyFor(changed));

  const beforeAbandon = registry.keyFor(first);
  registry.abandon(first);
  assert.notEqual(registry.keyFor(first), beforeAbandon);
});

test("reselecting the same upload content preserves its intent signature", async () => {
  const first = new File(["same-image-bytes"], "product.png", {
    type: "image/png",
  });
  const reselected = new File(["same-image-bytes"], "product.png", {
    type: "image/png",
  });
  const changed = new File(["changed-image-bytes"], "product.png", {
    type: "image/png",
  });
  assert.equal(
    await uploadIntentSignature(first, "image"),
    await uploadIntentSignature(reselected, "image"),
  );
  assert.notEqual(
    await uploadIntentSignature(first, "image"),
    await uploadIntentSignature(changed, "image"),
  );
  assert.notEqual(
    await uploadIntentSignature(first, "image"),
    await uploadIntentSignature(first, "mask"),
    "the same bytes used for a different media role are a new logical intent",
  );
});

test("job status never authorizes a missing server action", () => {
  const processingWithoutActions = {
    status: "processing",
    allowedActions: [],
  };
  assert.equal(
    allowsJobAction(processingWithoutActions.allowedActions, "cancel"),
    false,
  );
  assert.throws(
    () => assertJobAction(processingWithoutActions.allowedActions, "cancel"),
    /未授权/,
  );
  assert.equal(allowsJobAction(["cancel"], "cancel"), true);
});
