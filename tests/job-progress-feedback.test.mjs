import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import test from "node:test";
import { formatJobElapsed, jobElapsedMilliseconds, presentJobProgress } from "../lib/jobs/progress-feedback.ts";

const job = (changes = {}) => ({ id: "job-a", status: "processing", stage: "generating", progress: 0,
  succeeded_item_count: 0, expected_item_count: 21, created_at: "2026-10-22T10:00:00Z", finished_at: null, ...changes });

test("zero-progress active tasks show activity without inventing completed items", () => {
  const value = presentJobProgress(job());
  assert.equal(value.indeterminate, true);
  assert.equal(value.progress, 0);
  assert.equal(value.countText, "已完成 0 / 21 项");
  assert.equal(value.running, true);
  assert.equal(presentJobProgress(job({ progress: 35, succeeded_item_count: 3 })).progress, 35);
  assert.equal(presentJobProgress(job({ progress: 35, succeeded_item_count: 3 })).countText, "已完成 3 / 21 项");
});

test("terminal and user-action states override stale generating stage and stop activity", () => {
  for (const [status, title] of Object.entries({ completed: "任务已完成", failed: "任务暂未完成", partial: "部分结果已完成", cancelled: "任务已取消", awaiting_confirmation: "等待方案确认", awaiting_final_review: "等待检查与交付", draft: "草稿已保存" })) {
    const value = presentJobProgress(job({ status }));
    assert.equal(value.title, title);
    assert.equal(value.running, false);
    assert.equal(value.indeterminate, false);
  }
});

test("known common phases use user-facing copy, unknown provider stages stay generic", () => {
  assert.equal(presentJobProgress(job({ status: "queued" })).title, "任务已排队");
  assert.equal(presentJobProgress(job({ status: "planning" })).title, "正在规划画面");
  assert.equal(presentJobProgress(job({ stage: "validating" })).title, "正在检查结果");
  assert.equal(presentJobProgress(job({ stage: "organizing" })).title, "正在整理结果");
  assert.equal(presentJobProgress(job({ stage: "analyzing" })).title, "正在准备素材");
  assert.equal(presentJobProgress(job({ stage: "private_provider_checkpoint_9" })).title, "正在生成图片");
});

test("elapsed time survives refresh using the saved timestamp while completion stays fixed", () => {
  const task = job();
  assert.equal(jobElapsedMilliseconds(task, Date.parse("2026-10-22T10:02:05Z")), 125000);
  assert.equal(jobElapsedMilliseconds(task, Date.parse("2026-10-22T10:05:10Z")), 310000);
  assert.equal(presentJobProgress(task).progress, 0);
  const done = job({ status: "completed", finished_at: "2026-10-22T10:03:00Z" });
  assert.equal(jobElapsedMilliseconds(done, Date.parse("2026-10-23T10:00:00Z")), 180000);
  assert.equal(jobElapsedMilliseconds(done, Date.parse("2026-10-24T10:00:00Z")), 180000);
});

test("invalid timestamps and clock skew do not produce NaN or negative waiting time", () => {
  assert.equal(jobElapsedMilliseconds(job({ created_at: "invalid" }), Date.now()), null);
  assert.equal(jobElapsedMilliseconds(job(), null), null);
  assert.equal(jobElapsedMilliseconds(job(), 0), 0);
  assert.equal(jobElapsedMilliseconds(job({ status: "failed" }), Date.now()), null);
  assert.equal(presentJobProgress(job({ progress: NaN })).progress, null);
  assert.equal(presentJobProgress(job({ progress: 120 })).progress, 100);
  assert.equal(presentJobProgress(job({ progress: -10 })).progress, 0);
});

test("legacy timestamps without a zone represent UTC just like offset-aware records", () => {
  const now = Date.parse("2026-10-22T10:02:05Z");
  assert.equal(jobElapsedMilliseconds(job({ created_at: "2026-10-22T10:00:00.000000" }), now), 125000);
  assert.equal(jobElapsedMilliseconds(job({ created_at: "2026-10-22T18:00:00+08:00" }), now), 125000);
  assert.equal(jobElapsedMilliseconds(job({ status: "completed", created_at: "2026-10-22T10:00:00", finished_at: "2026-10-22T10:03:00" }), now), 180000);
});

test("elapsed labels remain readable for short and long tasks", () => {
  assert.equal(formatJobElapsed(59000), "59 秒");
  assert.equal(formatJobElapsed(125000), "2 分 5 秒");
  assert.equal(formatJobElapsed(3661000), "1 小时 1 分");
});

test("rendered feedback distinguishes indeterminate progress and preserves reconnection and failure details", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "polaris-progress-feedback-"));
  try {
    const output = path.join(directory, "render.cjs");
    await build({ stdin: { contents: 'import React from "react"; import {renderToStaticMarkup} from "react-dom/server"; import {JobProgressFeedback} from "./components/job-progress-feedback"; export const render=(props)=>renderToStaticMarkup(React.createElement(JobProgressFeedback,props));', resolveDir: process.cwd(), loader: "tsx" }, bundle: true, platform: "node", format: "cjs", packages: "bundle", jsx: "automatic", loader: { ".css": "empty" }, outfile: output, logLevel: "silent" });
    const { default: { render } } = await import(pathToFileURL(output).href);
    const queued = render({ job: job({ status: "queued" }), onRefresh() {} });
    assert.match(queued, /role="progressbar"/);
    assert.doesNotMatch(queued, /aria-valuenow=/);
    assert.match(queued, /已完成 0 \/ 21 项/);
    const partial = render({ job: job({ status: "partial", progress: 50, error_message: "保留的具体失败原因" }), error: new Error("网络响应超时"), onRefresh() {} });
    assert.match(partial, /aria-valuenow="50"/);
    assert.match(partial, /部分结果已完成/);
    assert.match(partial, /重新连接/);
    assert.match(partial, /保留的具体失败原因/);
    assert.match(partial, /网络响应超时/);
    assert.doesNotMatch(partial, /<details[^>]*open/);
    assert.doesNotMatch(partial, /data-moving="true"/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
