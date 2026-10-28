import type { Job, JobDetail } from "@/lib/api/types";

export type ProgressJob = Job | JobDetail;

const active = new Set(["queued", "planning", "processing", "cancelling"]);
const ended = new Set(["completed", "partial", "failed", "cancelled"]);

/** Presentation follows persisted state. Time never advances the completion value. */
export function presentJobProgress(job: ProgressJob) {
  const running = active.has(job.status);
  const terminal = ended.has(job.status);
  let title: string;
  let description: string;
  if (job.status === "completed") {
    title = "任务已完成";
    description = "可查看已保存的结果与交付信息。";
  } else if (job.status === "failed") {
    title = "任务暂未完成";
    description = "原始记录已保留，请查看原因及页面提供的重试操作。";
  } else if (job.status === "partial") {
    title = "部分结果已完成";
    description = "可查看已保存的结果，并按页面提示处理未完成项目。";
  } else if (job.status === "cancelled") {
    title = "任务已取消";
    description = "任务不会继续生成，已保存的记录仍可查看。";
  } else if (job.status === "cancelling") {
    title = "正在安全取消";
    description = "正在等待当前操作结束，状态会自动更新。";
  } else if (job.status === "awaiting_confirmation") {
    title = "等待方案确认";
    description = "方案已保存，请按页面提供的操作继续。";
  } else if (job.status === "awaiting_final_review") {
    title = "等待检查与交付";
    description = "请查看结果及页面提供的检查或交付操作。";
  } else if (job.status === "draft") {
    title = "草稿已保存";
    description = "任务尚未开始生成。";
  } else if (job.status === "queued") {
    title = "任务已排队";
    description = "正在等待可用的处理资源，无需重复提交。";
  } else if (job.status === "planning") {
    title = "正在规划画面";
    description = "正在分析素材、整理内容与生成要求。";
  } else if (["validating", "validation", "visual_review", "reviewing", "suite_reviewing", "suite_quality_review"].includes(job.stage ?? "")) {
    title = "正在检查结果";
    description = "正在检查已生成的图片，检查完成后会更新结果。";
  } else if (["organizing", "suite_quality_passed", "suite_quality_reported", "suite_review_reported"].includes(job.stage ?? "")) {
    title = "正在整理结果";
    description = "正在保存结果与交付信息。";
  } else if (["preparing", "analyzing", "analyzing_confirmed_sources"].includes(job.stage ?? "")) {
    title = "正在准备素材";
    description = "正在整理本次任务的素材与生成要求。";
  } else {
    title = "正在生成图片";
    description = "完成的项目会自动更新，可继续浏览其他页面。";
  }
  const progress = Number.isFinite(job.progress) ? Math.max(0, Math.min(100, job.progress)) : null;
  const count = Number.isFinite(job.succeeded_item_count) ? Math.max(0, job.succeeded_item_count) : 0;
  const total = Number.isFinite(job.expected_item_count) ? Math.max(0, job.expected_item_count) : 0;
  return {
    running, terminal, title, description, progress,
    countText: total ? `已完成 ${count} / ${total} 项` : `已完成 ${count} 项`,
    // Before the first reported progress, show activity without inventing a percentage.
    indeterminate: running && (progress === null || progress === 0),
  };
}

function timestamp(value: string | null | undefined): number | null {
  if (!value) return null;
  // Legacy API timestamps are stored in UTC but may omit their offset.
  const utcValue = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(value) ? `${value}Z` : value;
  const result = Date.parse(utcValue);
  return Number.isFinite(result) ? result : null;
}

export function jobElapsedMilliseconds(job: ProgressJob, now?: number | null): number | null {
  const start = timestamp(job.created_at);
  if (start === null) return null;
  const end = ended.has(job.status)
    ? timestamp("finished_at" in job ? job.finished_at : null)
    : now;
  if (end === null || end === undefined || !Number.isFinite(end)) return null;
  return Math.max(0, end - start);
}

export function formatJobElapsed(milliseconds: number): string {
  const seconds = Math.floor(Math.max(0, milliseconds) / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}
