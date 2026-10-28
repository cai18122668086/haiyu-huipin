import assert from "node:assert/strict";
import test from "node:test";
import { assetCreationHref, readAssetHandoff } from "../lib/assets/creation-handoff.ts";

test("asset handoff keeps original asset IDs, selection order and case identity", () => {
  const href = assetCreationHref("/workspace/detail-pack", "ws_test", ["ast_first", "asset_second"]);
  assert.deepEqual(readAssetHandoff(new URL(href, "http://localhost").searchParams, "ws_test", 8), { ids: ["ast_first", "asset_second"], caseId: undefined });
  const local = assetCreationHref("/workspace/local-edit", "ws_test", ["ast_first"], "insp_original");
  assert.equal(readAssetHandoff(new URL(local, "http://localhost").searchParams, "ws_test", 1).caseId, "insp_original");
});

test("handoff rejects wrong workspace, excess or invalid IDs and never overrides saved job inputs", () => {
  assert.throws(() => assetCreationHref("/workspace/local-edit", "ws_test", ["a", "b"]), /1–1/);
  assert.throws(() => assetCreationHref("/workspace/detail-pack", "ws_test", Array.from({length:9}, (_,i) => `ast_${i}`)), /1–8/);
  assert.throws(() => assetCreationHref("/workspace/local-edit", "ws_test", ["../foreign"]), /有效图片/);
  assert.throws(() => assetCreationHref("/workspace/local-edit", "ws_test", []), /先选择/);
  const params = new URLSearchParams("workspaceId=ws_other&assetId=ast_one");
  assert.throws(() => readAssetHandoff(params, "ws_test", 1), /当前工作区/);
  params.set("jobId", "job_original");
  assert.equal(readAssetHandoff(params, "ws_test", 1), undefined);
});
