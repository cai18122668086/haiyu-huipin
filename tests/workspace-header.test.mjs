import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("workspace header starts a fresh task instead of rendering an inert button", async () => {
  const source = await readFile("components/workspace-header.tsx", "utf8");

  assert.match(source, /type="button"/);
  assert.match(source, /onClick=\{startNewTask\}/);
  assert.match(source, /window\.location\.replace\(window\.location\.pathname\)/);
});
