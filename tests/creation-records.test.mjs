import assert from "node:assert/strict";
import test from "node:test";
import { mergeCreationRecords } from "../lib/jobs/creation-records.ts";
import { resolveJobWorkspaceHref } from "../lib/jobs/workspace-link-resolution.ts";

const record = {id:"rep_"+"a".repeat(32),status:"analysing",method:"style",created_at:"2026-10-25T10:00:00Z",updated_at:"2026-10-25T10:02:00Z",job_ids:[],progress:40,stage:"正在规划",expected:12,generated:0};

test("a replica exists in global records before there is an image job",()=>{
  const merged=mergeCreationRecords([], [record], "ws_test");
  assert.equal(merged.length,1);
  assert.equal(merged[0].status,"planning");
  assert.deepEqual(merged[0].allowed_actions,[]);
  assert.equal(resolveJobWorkspaceHref(merged[0],[]),`/style-replica?sessionId=${record.id}`);
});

test("job creation and retry do not duplicate the original replica record",()=>{
  const completed={id:"job_done",status:"completed",created_at:"2026-10-25T10:03:00Z",updated_at:"2026-10-25T10:05:00Z",expected_item_count:12,succeeded_item_count:12,failed_item_count:0,output_asset_count:12,progress:100,primary_output_asset_id:"asset_1",cost:{}};
  const merged=mergeCreationRecords([completed,{...completed,id:"job_old"},{...completed,id:"other"}], [{...record,status:"generating",job_ids:["job_done","job_old"]}],"ws_test");
  assert.equal(merged.length,2);
  const replica=merged.find(x=>x.id===record.id);
  assert.equal(replica.status,"completed");
  assert.equal(replica.primary_output_asset_id,"asset_1");
  assert.equal(replica.output_asset_count,12);
});

test("current server summary wins over a stale retry job in the client cache",()=>{
  const old={id:"job_old",status:"failed",created_at:record.created_at,updated_at:record.updated_at,progress:20};
  const merged=mergeCreationRecords([old], [{...record,status:"generating",job_ids:["job_new","job_old"],current_job_ids:["job_new"],progress:65}],"ws_test");
  assert.equal(merged.length,1);
  assert.equal(merged[0].status,"processing");
  assert.equal(merged[0].progress,65);
});
