import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";
import { mergeReplicaProgress, replicaAnalysisMessage, replicaOverallProgress, replicaPlanFailure, replicaSegmentationFailure, replicaWorkflowStep } from "../lib/replica/progress.ts";

const doc = (progress, extra = {}) => ({ id: "rep_test", status: "analysing", payload: { analysis_attempt_id: "attempt-1", progress }, ...extra });

test("exhausted quality reviews explain retained plans instead of suggesting another transport retry", () => {
  const failed = doc(82, {status:"failed"});
  failed.payload.error_code = "REPLICA_PLAN_AUDIT_REJECTED";
  failed.payload.analysis_progress = {stage:"review_plan",total:12,planned:12,reviewed:12,accepted:9,pending_corrections:3};
  const failure = replicaPlanFailure(failed);
  assert.equal(failure.title, "还有 3 屏方案待调整");
  assert.match(failure.message, /已通过的 9 屏方案/);
  assert.match(failure.message, /请先调整/);
  delete failed.payload.analysis_progress;
  assert.equal(replicaPlanFailure(failed).title, "部分方案需要调整");
  failed.payload.error_code = "REPLICA_ANALYSIS_UNAVAILABLE";
  assert.equal(replicaPlanFailure(failed), undefined);
});

test("planning progress explains retained work without presenting a failed screen as a restart", () => {
  const current = doc(82);
  current.payload.analysis_progress = { stage: "review_plan", total: 12, planned: 12, reviewed: 12, accepted: 8, pending_corrections: 4 };
  assert.equal(replicaAnalysisMessage(current), "已确认 8 / 12 屏方案，正在处理剩余部分");
  current.payload.analysis_progress.accepted = 12;
  assert.equal(replicaAnalysisMessage(current), "12 屏方案已确认，正在准备生成");
});

test("replanning and delayed poll responses do not move progress backwards", () => {
  let current;
  for (const progress of [12, 48, 82, 48, 82]) current = mergeReplicaProgress(current, doc(progress));
  assert.equal(current.payload.progress, 82);
  assert.equal(mergeReplicaProgress(doc(82, {updated_at:"2026-10-23T13:00:00Z"}), doc(48, {updated_at:"2026-10-23T12:00:00Z"})).payload.progress, 82);
});

test("SQLite timestamps and UTC creation responses preserve polling order in every browser timezone", () => {
  const moduleUrl = new URL("../lib/replica/progress.ts", import.meta.url).href;
  const script = `
    import assert from 'node:assert/strict';
    import {mergeReplicaProgress} from ${JSON.stringify(moduleUrl)};
    const initial = {id:'rep_live',status:'analysing',updated_at:'2026-10-26T14:04:48.306014+00:00',payload:{analysis_attempt_id:'same',progress:0}};
    const complete = {...initial,status:'completed',updated_at:'2026-10-26T14:12:00.782398',payload:{...initial.payload,progress:100}};
    assert.equal(mergeReplicaProgress(initial,complete).status,'completed');
    const delayed = {...initial,updated_at:'2026-10-26T14:05:00.000000Z'};
    assert.equal(mergeReplicaProgress(complete,delayed).status,'completed');
    assert.equal(mergeReplicaProgress(complete,delayed).payload.progress,100);
  `;
  for (const timezone of ["Asia/Shanghai", "UTC", "America/New_York"]) {
    execFileSync(process.execPath, ["--experimental-strip-types", "--input-type=module", "-e", script], {
      env: { ...process.env, TZ: timezone },
      stdio: "pipe",
    });
  }
});

test("an explicitly new analysis or job may reset progress", () => {
  const next = doc(0); next.payload.analysis_attempt_id = "attempt-2";
  assert.equal(mergeReplicaProgress(doc(82), next).payload.progress, 0);
  const retry = doc(25); retry.payload.job_ids = ["job_new"];
  assert.equal(mergeReplicaProgress(doc(82), retry).payload.progress, 25);
});

test("a terminal failure is not revived by an old generating reply", () => {
  const failed = doc(82, {status:"failed"});
  assert.equal(mergeReplicaProgress(failed, doc(70, {status:"generating"})).status, "failed");
});

test("a delayed reply for the old job cannot replace a newer retry", () => {
  const current = doc(25, {updated_at:"2026-10-23T13:00:00Z"});
  current.payload.job_ids = ["job_retry"];
  const stale = doc(95, {updated_at:"2026-10-23T12:00:00Z"});
  stale.payload.job_ids = ["job_old"];
  assert.deepEqual(mergeReplicaProgress(current, stale).payload.job_ids, ["job_retry"]);
});

test("analysis completion and generation share one scale, failure is not success 100%", () => {
  assert.equal(replicaOverallProgress(doc(92)), 22);
  const generated = doc(100, {status:"generating"});
  generated.payload.job_ids = ["job_new"];
  generated.payload.execution_progress = {percentage:25};
  assert.equal(replicaOverallProgress(generated), 25);
  assert.equal(replicaOverallProgress({...generated,status:"failed"}), 25);
  assert.equal(replicaOverallProgress({...generated,status:"completed"}), 100);
});

test("failed, partial and cancelled suites never mark successful delivery in the stepper", () => {
  for (const status of ["failed", "partial", "cancelled", "processing"]) {
    const current = doc(95, {status}); current.payload.job_ids = ["job_suite"];
    assert.equal(replicaWorkflowStep(current, {job_suite:status}), 4);
  }
  const success = doc(100, {status:"completed"}); success.payload.job_ids = ["job_suite"];
  assert.equal(replicaWorkflowStep(success, {job_suite:"completed"}), 5);
  success.payload.submission_complete = false;
  assert.equal(replicaWorkflowStep(success, {job_suite:"completed"}), 4);
});


test("segmentation failures explain affected references without advertising a blind resume", () => {
  const failed = doc(12, {status:"failed"});
  failed.payload.error_code = "REPLICA_SEGMENTATION_INVALID";
  failed.payload.segmentation_diagnostics = {coverage_valid:true, columns:[
    {reference_index:2,split_count:2,inventory_count:4,reason:"count"},
    {reference_index:3,split_count:2,inventory_count:2,reason:"boundaries"}]};
  const failure = replicaSegmentationFailure(failed);
  assert.match(failure.message, /第 3 张参考图：拆屏 2 屏，复核 4 屏/);
  assert.match(failure.message, /第 4 张参考图的分屏边界位置不一致/);
  assert.equal(replicaPlanFailure(failed), undefined);
  failed.payload.segmentation_diagnostics.coverage_valid = false;
  assert.match(replicaSegmentationFailure(failed).message, /遗漏、重叠或裁切/);
  failed.payload.error_code = "REPLICA_ANALYSIS_UNAVAILABLE";
  assert.equal(replicaSegmentationFailure(failed), undefined);
});


test("legacy style failures retain materials without claiming a missing analysis checkpoint", () => {
  const failed = doc(92, {status:"failed"});
  Object.assign(failed.payload, {error_code:"REPLICA_PLAN_AUDIT_REJECTED",replication_method:"style",analysis_revision_available:false});
  const failure = replicaPlanFailure(failed);
  assert.equal(failure.title, "风格方案需要调整");
  assert.match(failure.message, /可使用现有素材重新诊断/);
  assert.doesNotMatch(failure.message, /已完成的规划/);
  failed.payload.analysis_revision_available = true;
  assert.match(replicaPlanFailure(failed).message, /已完成的规划/);
});
