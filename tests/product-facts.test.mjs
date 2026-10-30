import test from "node:test";
import assert from "node:assert/strict";
import { splitProductFacts } from "../lib/product-facts.ts";
import { mergeCreationRecords } from "../lib/jobs/creation-records.ts";

test("long product descriptions keep every fact across the structured field limit", () => {
  const facts = Array.from({length: 40}, (_, i) => `真实卖点${i + 1}`);
  const result = splitProductFacts(facts.join("\n"), 12);
  assert.equal(result.length, 12);
  assert.deepEqual(result.flatMap(value => value.split("\n")), facts);
});

test("server replica summary reports suite progress and price instead of the first child", () => {
  const record = {id:"rep_suite",status:"generating",method:"exact",created_at:"2026-10-30",updated_at:"2026-10-30",job_ids:["job_one"],current_job_ids:["job_one"],progress:60,stage:null,expected:8,generated:5,failed:1,estimated_credits:64,actual_credits:null};
  const result = mergeCreationRecords([{id:"job_one",status:"completed",succeeded_item_count:1,failed_item_count:0,cost:{actual_credits:8},created_at:"2026-10-30"}], [record], "ws_test")[0];
  assert.equal(result.succeeded_item_count,5);
  assert.equal(result.failed_item_count,1);
  assert.equal(result.cost.estimated_credits,64);
  assert.equal(result.cost.actual_credits,null);
});
