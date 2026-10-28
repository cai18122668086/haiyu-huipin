import assert from "node:assert/strict";
import test from "node:test";
import { canDownloadJobArchive } from "../lib/jobs/results.ts";

const job = (type, status = "completed") => ({ type, status, expected_item_count:1, succeeded_item_count:1, failed_item_count:0, items:[], assets:[{asset_id:"asset_result",direction:"output",is_deliverable:true,ordinal:0}] });

test("archive action follows supported backend task types and complete outputs", () => {
  for (const type of ["local_edit", "product_image", "reference_remix", "creative_image", "model_pose_transfer", "product_replica"]) assert.equal(canDownloadJobArchive(job(type)), false);
  for (const type of ["detail_pack", "amazon_visual_suite", "domestic_main_image"]) assert.equal(canDownloadJobArchive(job(type)), true);
  assert.equal(canDownloadJobArchive({...job("detail_pack"), assets:[]}), false);
  assert.equal(canDownloadJobArchive(job("detail_pack", "awaiting_final_review")), false);
  assert.equal(canDownloadJobArchive(job("detail_pack", "partial")), false);
});

test("preserved Amazon infrastructure failures keep download access without authorizing image failures", () => {
  const partial = {...job("amazon_visual_suite", "partial"), succeeded_item_count:0, failed_item_count:1, items:[{status:"failed", error_details:{failure_kind:"review_infrastructure_failure",image_quality_failed:false,generated_asset_preserved:true}}]};
  assert.equal(canDownloadJobArchive(partial), true);
  partial.items[0].error_details.image_quality_failed = true;
  assert.equal(canDownloadJobArchive(partial), false);
});
