import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { resolveJobWorkspaceHref } from "../lib/jobs/workspace-link-resolution.ts";

test("native and legacy replica jobs restore the authenticated replica session", () => {
  const href = resolveJobWorkspaceHref({id:"job_test",type:"reference_remix",configuration:{replica_session_id:"rep_test"}}, []);
  assert.equal(href, "/style-replica?sessionId=rep_test");
  assert.equal(resolveJobWorkspaceHref({id:"job_list",type:"reference_remix",display_name:"一键复刻 · 风格复刻"}, []), "/style-replica?jobId=job_list");
  assert.equal(resolveJobWorkspaceHref({id:"job_test",type:"reference_remix"}, [{jobType:"reference_remix",route:"/workspace/reference-remix"}]), "/workspace/reference-remix?jobId=job_test");
});

test("the replica source selector and execution modes have separate state", async () => {
  const source = await readFile(new URL("../components/style-replica-workspace.tsx", import.meta.url), "utf8");
  assert.match(source, /replication_method: replicationMethod/);
  assert.match(source, /aria-label="复刻方式"/);
  assert.match(source, /get\("sessionId"\)/);
  assert.doesNotMatch(source, /href=\{`\/workspace\/reference-remix/);
});
