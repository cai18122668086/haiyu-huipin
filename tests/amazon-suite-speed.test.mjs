import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  amazonSuiteSpeedConfiguration,
  amazonSuiteSpeedDraftKey,
  readAmazonSuiteSpeed,
} from "../lib/amazon-suite-speed.ts";

test("Amazon historical and absent version values retain the original request identity", () => {
  for (const value of [undefined, null, "standard", "", "unknown", false, { amazon_speed: "fast" }]) {
    assert.equal(readAmazonSuiteSpeed(value), "standard");
    assert.deepEqual(amazonSuiteSpeedConfiguration(readAmazonSuiteSpeed(value)), {});
  }
  assert.deepEqual(amazonSuiteSpeedConfiguration(readAmazonSuiteSpeed("fast")), { amazon_speed: "fast" });
});

test("Amazon speed drafts are scoped to their workspace", () => {
  assert.notEqual(amazonSuiteSpeedDraftKey("workspace-a"), amazonSuiteSpeedDraftKey("workspace-b"));
  const draftStore = new Map([[amazonSuiteSpeedDraftKey("workspace-a"), "fast"]]);
  assert.equal(readAmazonSuiteSpeed(draftStore.get(amazonSuiteSpeedDraftKey("workspace-a"))), "fast");
  assert.equal(readAmazonSuiteSpeed(draftStore.get(amazonSuiteSpeedDraftKey("workspace-b"))), "standard");
});

test("Amazon UI restores saved version and shows fast first without internal process notices", async () => {
  const source = await readFile(new URL("../components/commerce-skill-workspace.tsx", import.meta.url), "utf8");
  assert.match(source, /useState<AmazonSuiteSpeed>\("standard"\)/);
  assert.match(source, /jobId \? readAmazonSuiteSpeed\(saved.text\("amazon_speed"\)\) : draftAmazonSpeed/);
  assert.match(source, /\.\.\.amazonSuiteSpeedConfiguration\(amazonSpeed\)/);
  assert.match(source, /className=\{amazonStyles.speedOptions\} disabled=\{Boolean\(jobId\)\}/);
  assert.match(source, /\["fast", "快速版（约5分钟 · 8积分\/张）"\],\s*\["standard", "精致版（约40分钟 · 15积分\/张）"\]/);
  assert.doesNotMatch(source, /技术校验完成，未进行 AI 视觉审核|不进行 AI 视觉审核或自动审美重绘/);
});
