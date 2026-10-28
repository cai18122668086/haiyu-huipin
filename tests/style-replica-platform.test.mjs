import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(
  new URL("../components/style-replica-workspace.tsx", import.meta.url),
  "utf8",
);

test("source selection advertises only supported data providers", () => {
  assert.match(source, /label="来源平台"[^\n]+options=\{\[\["auto", "从链接自动识别"\], \["taobao", "淘宝"\], \["tmall", "天猫"\], \["1688", "1688"\], \["jd", "京东"\]\]\}/);
  assert.match(source, /风格参考图/);
});
