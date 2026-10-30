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
  assert.match(source, /<GenerationVersionField name="amazon-suite-speed"[^>]*value=\{amazonSpeed\}[^>]*onChange=\{selectAmazonSpeed\}[^>]*disabled=\{Boolean\(jobId\)\}/);
  const controls = await readFile(new URL("../components/creation-settings.tsx", import.meta.url), "utf8");
  assert.match(controls, /value: "fast", label: "快速版"[\s\S]*price: 8[\s\S]*value: "standard", label: "精致版"[\s\S]*price: 15/);
  assert.doesNotMatch(source, /技术校验完成，未进行 AI 视觉审核|不进行 AI 视觉审核或自动审美重绘/);
});
