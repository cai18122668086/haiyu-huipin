import assert from "node:assert/strict";
import test from "node:test";
import { DETAIL_SPEED_OPTIONS, detailSpeedSettings, savedDetailSpeed } from "../lib/detail-speed.ts";
import { detailSkillSettings, savedDetailSkill } from "../lib/detail-skills.ts";
import { intentSignature } from "../lib/api/idempotency.ts";

test("domestic detail offers original first and an independent fast option", () => {
  assert.deepEqual(DETAIL_SPEED_OPTIONS.map(option => option.value), ["standard", "fast"]);
  assert.deepEqual(DETAIL_SPEED_OPTIONS.map(option => option.title), ["原版", "快速"]);
});

test("default keeps the original request identity while fast selects a new intent", () => {
  const original = { detail_skill: "refined", page_count: 8, size: "1440x1920" };
  const standard = { ...original, ...detailSpeedSettings("domestic", "standard") };
  const fast = { ...original, ...detailSpeedSettings("domestic", "fast") };
  assert.deepEqual(standard, original);
  assert.deepEqual(fast, { ...original, detail_speed: "fast" });
  assert.equal(intentSignature("create-job", standard), intentSignature("create-job", original));
  assert.notEqual(intentSignature("create-job", fast), intentSignature("create-job", original));
  assert.deepEqual({ ...detailSkillSettings("domestic", "refined"), ...detailSpeedSettings("domestic", "fast") }, {
    detail_skill: "refined", detail_speed: "fast",
  });
});

test("switching regions excludes fast from crossborder requests", () => {
  assert.deepEqual(detailSpeedSettings("international", "fast"), {});
  assert.deepEqual(detailSpeedSettings("international", "standard"), {});
});

test("saved server pipeline restores fast and never reinterprets historical quick jobs", () => {
  const fast = { domestic_detail_pipeline: "xinghe-domestic-detail-v4.0-fast" };
  assert.equal(savedDetailSpeed(fast), "fast");
  assert.equal(savedDetailSkill(fast), "refined");
  for (const configuration of [undefined, {}, { domestic_detail_pipeline: "xinghe-domestic-detail-v4.0" }, { domestic_detail_pipeline: "aurora-domestic-detail-v4.3.1" }]) {
    assert.equal(savedDetailSpeed(configuration), "standard");
  }
  assert.equal(savedDetailSkill({ domestic_detail_pipeline: "aurora-domestic-detail-v4.3.1" }), "quick");
  assert.equal(savedDetailSpeed({ settings: { detail_speed: "fast" } }), "standard");
});
