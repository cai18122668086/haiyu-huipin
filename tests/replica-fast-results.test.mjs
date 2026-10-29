import assert from "node:assert/strict";
import test from "node:test";
import { fastReplicaOutputAsset } from "../lib/replica/result-assets.ts";

const items = [{ id: "first", ordinal: 0 }, { id: "second", ordinal: 1 }];
const second = { id: "image-second", direction: "output", is_deliverable: true, job_item_id: "second" };

test("a failed first screen cannot display the successful second screen", () => {
  assert.equal(fastReplicaOutputAsset(items, [second], 0), undefined);
  assert.equal(fastReplicaOutputAsset(items, [second], 1), second);
});

test("out-of-order completion and private artifacts preserve screen identity", () => {
  const first = { ...second, id: "image-first", job_item_id: "first" };
  const assets = [{ ...first, is_deliverable: false }, second, first];
  assert.equal(fastReplicaOutputAsset(items, assets, 0), first);
  assert.equal(fastReplicaOutputAsset(items, assets, 1), second);
  assert.equal(fastReplicaOutputAsset(items, assets, 2), undefined);
});

test("a retried screen uses the new job item while a reused screen keeps its position", () => {
  const retryItems = [{ id: "reused-first", ordinal: 0 }, { id: "retry-second", ordinal: 1 }];
  const first = { ...second, job_item_id: "reused-first" };
  const retrySecond = { ...second, job_item_id: "retry-second" };
  assert.equal(fastReplicaOutputAsset(retryItems, [retrySecond, first], 0), first);
  assert.equal(fastReplicaOutputAsset(retryItems, [retrySecond, first], 1), retrySecond);
});
