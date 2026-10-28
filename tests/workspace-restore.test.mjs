import assert from "node:assert/strict";
import test from "node:test";
import { workspaceJobSnapshot, matchesWorkspaceJob } from "../lib/jobs/workspace-restore.ts";

test("history restores server settings and input roles without promoting output or mask assets", () => {
  const job = { configuration: {
    settings: { platform_code: "jd", task_type: "change_color", aspect_ratio: "3:4", image_count: 2, target_color: "深蓝色", instruction: "保留标签", selling_points: ["防漏", "便携"] },
    input: { product_asset_ids: ["product"], reference_asset_ids: ["reference"], source_asset_id: "source", selection_mask_asset_id: "mask" },
  }, assets: [{ asset_id: "output", direction: "output" }] };
  const saved = workspaceJobSnapshot(job);
  assert.equal(saved.text("platform_code"), "jd");
  assert.equal(saved.text("target_color"), "深蓝色");
  assert.equal(saved.choice("aspect_ratio", ["1:1", "3:4"], "1:1"), "3:4");
  assert.equal(saved.number("image_count", 1), 2);
  assert.equal(saved.lines("selling_points"), "防漏\n便携");
  assert.deepEqual(saved.ids("product_asset_ids"), ["product"]);
  assert.deepEqual(saved.ids("reference_asset_ids"), ["reference"]);
  assert.equal(saved.id("source_asset_id"), "source");
  assert.equal(saved.id("selection_mask_asset_id"), "mask");
  assert.deepEqual(saved.ids("result_asset_ids"), []);
});

test("switching task snapshots does not carry parameters or assets from the previous task", () => {
  const first = workspaceJobSnapshot({ configuration: { settings: { instruction: "A", target_color: "红色" }, input: { product_asset_ids: ["A"] } } });
  const second = workspaceJobSnapshot({ configuration: { settings: { instruction: "B" }, input: { reference_asset_ids: ["B"] } } });
  assert.equal(first.text("instruction"), "A");
  assert.equal(second.text("instruction"), "B");
  assert.equal(second.text("target_color"), "");
  assert.deepEqual(second.ids("product_asset_ids"), []);
  assert.deepEqual(second.ids("reference_asset_ids"), ["B"]);
});

test("missing or malformed saved sections remain empty instead of inventing source selections", () => {
  for (const configuration of [{}, { settings: null, input: [] }, { settings: "invalid", input: 3 }]) {
    const saved = workspaceJobSnapshot({ configuration });
    assert.equal(saved.text("instruction"), "");
    assert.deepEqual(saved.ids("product_asset_ids"), []);
    assert.equal(saved.id("source_asset_id"), undefined);
  }
  assert.equal(workspaceJobSnapshot().text("instruction"), "");
});

test("saved values validate primitive types and enum options", () => {
  const saved = workspaceJobSnapshot({ configuration: { settings: { instruction: 42, image_count: Infinity, aspect_ratio: "bad", selling_points: ["one", null, 2, "two"] }, input: { product_asset_ids: ["one", null, "", 1, "two"], source_asset_id: ["wrong"] } } });
  assert.equal(saved.text("instruction"), "");
  assert.equal(saved.number("image_count", 1), 1);
  assert.equal(saved.choice("aspect_ratio", ["1:1", "3:4"], "1:1"), "1:1");
  assert.equal(saved.lines("selling_points"), "one\ntwo");
  assert.deepEqual(saved.ids("product_asset_ids"), ["one", "two"]);
  assert.equal(saved.id("source_asset_id"), undefined);
});

test("a task must match both the workspace type and the product preset", () => {
  const task = { type: "product_image", task_preset: "change_color", configuration: { settings: { task_type: "change_color" } } };
  assert.equal(matchesWorkspaceJob(task, "product_image", "change_color"), true);
  assert.equal(matchesWorkspaceJob(task, "product_image", "change_background"), false);
  assert.equal(matchesWorkspaceJob(task, "local_edit"), false);
  assert.equal(matchesWorkspaceJob({ ...task, task_preset: undefined }, "product_image", "change_color"), true);
  assert.equal(matchesWorkspaceJob({ ...task, task_preset: undefined, configuration: {} }, "product_image", "change_color"), false);
});

test("model pose history keeps the full resolution source, pose and catalog identity separate", () => {
  const saved = workspaceJobSnapshot({ configuration: { settings: { instruction: "保持服装" }, input: { model_source_asset_id: "model", pose_reference_asset_id: "pose-original", pose_slug: "male-003", pose_reference_sha256: "hash" } } });
  assert.equal(saved.id("model_source_asset_id"), "model");
  assert.equal(saved.id("pose_reference_asset_id"), "pose-original");
  assert.equal(saved.id("pose_slug"), "male-003");
  assert.deepEqual(saved.ids("product_asset_ids"), []);
});
