import assert from "node:assert/strict";
import test from "node:test";
import { DETAIL_SKILLS, detailSkillSettings, savedDetailSkill } from "../lib/detail-skills.ts";

test("new website offers only refined detail", () => {
  assert.deepEqual(DETAIL_SKILLS.map(option => option.title), ["精致"]);
  assert.doesNotMatch(JSON.stringify(DETAIL_SKILLS), /星河|Aurora|Xinghe|4\.0|skill/i);
});

test("choosing refined does not send the quick planning contract", () => {
  assert.deepEqual(detailSkillSettings("domestic", "quick"), {
    detail_skill: "quick", website_contract_version: "WEBSITE_DIRECT_V2",
  });
  assert.deepEqual(detailSkillSettings("domestic", "refined"), { detail_skill: "refined" });
  assert.deepEqual(detailSkillSettings("international", "refined"), {});
});

test("saved task identity takes precedence over unsaved form state", () => {
  assert.equal(savedDetailSkill(), "quick");
  assert.equal(savedDetailSkill({ settings: {} }), "quick");
  assert.equal(savedDetailSkill({ domestic_detail_pipeline: "aurora-domestic-detail-v4.3.1" }), "quick");
  assert.equal(savedDetailSkill({ domestic_detail_pipeline: "xinghe-domestic-detail-v4.0" }), "refined");
});
