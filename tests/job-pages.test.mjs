import assert from "node:assert/strict";
import test from "node:test";
import { collectCursorPages } from "../lib/api/cursor-pages.ts";

test("history loads beyond 100 records without dropping legacy or boundary records", async () => {
  const jobs = Array.from({ length: 146 }, (_, index) => ({ id: `job_${index}`, type: index === 120 ? "product_replica" : "product_image" }));
  const cursors = [];
  const result = await collectCursorPages(async cursor => {
    cursors.push(cursor);
    return cursor ? { items: jobs.slice(99), has_more: false, next_cursor: null }
      : { items: jobs.slice(0, 100), has_more: true, next_cursor: "older", total: 146 };
  }, "分页异常");
  assert.deepEqual(cursors, [undefined, "older"]);
  assert.equal(result.items.length, 146);
  assert.equal(result.items[120].type, "product_replica");
  assert.equal(result.has_more, false);
});

test("history fails visibly when an older page fails or repeats its cursor", async () => {
  await assert.rejects(collectCursorPages(async () => ({ items: [], has_more: true, next_cursor: "same" }), "分页异常"), /分页异常/);
  await assert.rejects(collectCursorPages(async cursor => {
    if (cursor) throw new Error("history unavailable");
    return { items: [{ id: "first" }], has_more: true, next_cursor: "older" };
  }, "分页异常"), /history unavailable/);
});
