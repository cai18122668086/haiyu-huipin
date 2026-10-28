"use client";

import { AlertTriangle, RotateCcw } from "lucide-react";
import { StatusBadge } from "@/components/status-badge";
import type { JobDetail, JobStatus } from "@/lib/api/types";
import { allowsJobAction } from "@/lib/jobs/actions";
import { jobAssetRoleLabel, jobItemStatusLabel, safeJobFailureSummary } from "@/lib/jobs/presentation";

function itemBadgeStatus(status: JobDetail["items"][number]["status"]): JobStatus {
  switch (status) { case "succeeded": case "reused": return "completed"; case "running": return "processing"; case "queued": return "queued"; case "cancelled": return "cancelled"; case "failed": return "failed"; default: return "draft"; }
}

export function JobItemList({ job, onRetryItem, retryPending }: { job: JobDetail; onRetryItem: (itemId: string) => void; retryPending: boolean }) {
  if (!job.items.length) return <div className="job-empty-panel">暂无可展示的项目。</div>;
  return <div className="job-item-list">{job.items.map((item) => {
    const itemAssets = job.assets.filter((asset) => asset.job_item_id === item.id);
    const outputs = itemAssets.filter((asset) => asset.direction === "output" && asset.is_deliverable);
    const intermediateCount = itemAssets.filter((asset) => asset.direction === "intermediate" || (asset.direction === "output" && !asset.is_deliverable)).length;
    const roles = itemAssets.filter((asset) => asset.direction === "input").map((asset) => jobAssetRoleLabel(asset.role));
    const canRetry = item.status === "failed" && allowsJobAction(job.allowed_actions, "retry");
    return <article className="job-item-card" key={item.id}>
      <div className="job-item-card__head"><div><span className="job-item-card__ordinal">#{item.ordinal + 1}</span><strong>{item.item_key}</strong><p>{roles.length ? roles.join("、") : "未关联输入素材"}</p></div><div className="job-item-card__status"><StatusBadge status={itemBadgeStatus(item.status)} /><span>{jobItemStatusLabel(item.status)}</span></div></div>
      {item.stage && <div className="job-item-card__stage">处理阶段：{item.stage}</div>}
      {(outputs.length > 0 || intermediateCount > 0) && <div className="job-item-card__assets"><span>可交付输出：{outputs.length}</span>{intermediateCount > 0 ? <span>中间产物：{intermediateCount}</span> : null}</div>}
      {item.status === "failed" && <div className="job-item-failure" role="alert"><AlertTriangle size={16} /><div><strong>{safeJobFailureSummary(item.error_code)}</strong>{item.error_code && <small>错误码：{item.error_code}</small>}</div>{canRetry && <button type="button" className="secondary-button" onClick={() => onRetryItem(item.id)} disabled={retryPending}><RotateCcw size={14} />重试此项</button>}</div>}
    </article>;
  })}</div>;
}
