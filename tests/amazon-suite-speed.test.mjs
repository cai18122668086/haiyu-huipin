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

test("Amazon UI restores saved version, locks historical jobs and discloses unreviewed delivery", async () => {
  const source = await readFile(new URL("../components/commerce-skill-workspace.tsx", import.meta.url), "utf8");
  assert.match(source, /useState<AmazonSuiteSpeed>\("standard"\)/);
  assert.match(source, /jobId \? readAmazonSuiteSpeed\(saved.text\("amazon_speed"\)\) : draftAmazonSpeed/);
  assert.match(source, /\.\.\.amazonSuiteSpeedConfiguration\(amazonSpeed\)/);
  assert.match(source, /className=\{amazonStyles.speedOptions\} disabled=\{Boolean\(jobId\)\}/);
  assert.match(source, /NOT_REVIEWED: \{\s*title: "技术校验完成，未进行 AI 视觉审核"/);
  assert.match(source, /完整生成 21 张图片，桌面与移动 A\+ 各自独立构图/);
  assert.match(source, /不进行 AI 视觉审核或自动审美重绘/);
  assert.match(source, /模型和画质设置保持一致/);
  assert.match(source, /原图按实际尺寸和格式交付，不裁切或放大/);
});
