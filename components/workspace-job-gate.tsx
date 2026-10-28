"use client";

import { FileWarning, LoaderCircle } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { JobAssetPreview } from "@/components/job-asset-preview";
import { AppShell } from "@/components/app-shell";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { errorMessage } from "@/lib/api/request";
import type { JobDetail } from "@/lib/api/types";
import { matchesWorkspaceJob } from "@/lib/jobs/workspace-restore";
import { jobWorkspaceHref } from "@/lib/jobs/workspace-link";

export function WorkspaceJobGate({ jobId, job, error, onRetry, expectedType, expectedPreset, children }: {
  jobId?: string;
  job?: JobDetail;
  error: unknown;
  onRetry: () => void;
  expectedType: string;
  expectedPreset?: string;
  children: ReactNode;
}) {
  const workspace = useWorkspace();
  if (!jobId) return children;
  const requestError = workspace.error ?? error;
  const mismatch = job && (job.id !== jobId || !matchesWorkspaceJob(job, expectedType, expectedPreset));
  if (job && !mismatch) return children;
  return <AppShell sidebar={false}>
    <div className="content-page">
      <div className={`api-state${requestError || mismatch ? " api-state--error" : ""}`} role={requestError || mismatch ? "alert" : "status"}>
        {requestError || mismatch ? <FileWarning size={26} /> : <LoaderCircle className="spin" size={26} />}
        <div><h1>{mismatch ? "此任务属于其他工作台" : requestError ? "任务暂时无法加载" : "正在恢复任务"}</h1>
          <p>{mismatch ? "请打开任务对应的工作台，或在任务详情中查看原始记录。" : requestError ? errorMessage(requestError) : "正在读取已保存的素材、参数与生成进度。"}</p>
          <p>任务编号：{jobId}</p></div>
        {Boolean(requestError) && <button className="secondary-button" onClick={() => workspace.error ? void workspace.refetch() : onRetry()}>重试加载</button>}
        {mismatch && job && <Link className="secondary-button" href={jobWorkspaceHref(job)}>打开对应工作台</Link>}
        <Link className="secondary-button" href={`/jobs?jobId=${encodeURIComponent(jobId)}`}>查看任务详情</Link>
      </div>
    </div>
  </AppShell>;
}

export function WorkspaceJobInputs({ workspaceId, job }: { workspaceId?: string; job?: JobDetail }) {
  if (!workspaceId || !job) return null;
  const inputs = job.assets.filter((asset) => asset.direction === "input");
  return <section className="job-detail-section" aria-label="本次任务的素材">
    <h3>本次任务的素材</h3>
    <div className="job-asset-grid">{inputs.map((relation) => <JobAssetPreview key={`${relation.asset_id}:${relation.role}:${relation.ordinal}`} workspaceId={workspaceId} relation={relation} thumbnail />)}</div>
    {!inputs.length && <p>本任务没有登记输入素材。</p>}
  </section>;
}
