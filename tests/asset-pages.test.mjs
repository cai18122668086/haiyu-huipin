import assert from "node:assert/strict";
import test from "node:test";
import { collectAssetPages } from "../lib/api/asset-pages.ts";

test("asset library includes older pages and deduplicates boundary records", async () => {
  const calls = [];
  const result = await collectAssetPages(async (cursor) => {
    calls.push(cursor);
    return cursor
      ? { items: [{ id: "new" }, { id: "older" }], has_more: false, next_cursor: null }
      : { items: [{ id: "new" }], has_more: true, next_cursor: "older-page", total: 2 };
  });
  assert.deepEqual(calls, [undefined, "older-page"]);
  assert.deepEqual(result.items.map((item) => item.id), ["new", "older"]);
  assert.equal(result.total, 2);
  assert.equal(result.has_more, false);
});

test("asset library rejects broken pagination instead of reporting an incomplete library", async () => {
  await assert.rejects(collectAssetPages(async () => ({ items: [], has_more: true, next_cursor: "same" })), /分页异常/);
  await assert.rejects(collectAssetPages(async () => ({ items: [], has_more: true, next_cursor: null })), /分页异常/);
  await assert.rejects(collectAssetPages(async (cursor) => {
    if (cursor) throw new Error("backend unavailable");
    return { items: [{ id: "new" }], has_more: true, next_cursor: "older" };
  }), /backend unavailable/);
});
