"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ArrowUpRight,
  Ban,
  CheckCircle2,
  Clock3,
  FileWarning,
  LayoutGrid,
  List,
  LoaderCircle,
  RotateCcw,
  Search,
  Sparkles,
  TimerReset,
} from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { StatusBadge } from "@/components/status-badge";
import {
  useCancelJob,
  useConfirmPlan,
  useRetryJob,
} from "@/features/jobs/api/jobs";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { errorMessage } from "@/lib/api/request";
import type { Job, JobDetail, JobStatus } from "@/lib/api/types";
import {
  formatDate,
  jobCount,
  jobNote,
  jobTitle,
} from "@/lib/jobs/presentation";
import { allowsJobAction } from "@/lib/jobs/actions";
import { JobDetailPanel } from "@/features/jobs/components/job-detail-panel";
import { JobRecordCover } from "@/features/jobs/components/job-record-cover";
import { jobWorkspaceHref } from "@/lib/jobs/workspace-link";
import { useCreationRecords } from "@/features/jobs/api/creation-records";

const tabFilters: Record<string, JobStatus[] | undefined> = {
  all: undefined,
  active: ["queued", "planning", "awaiting_confirmation", "processing", "cancelling"],
  completed: ["completed"],
  attention: ["partial", "failed", "awaiting_confirmation", "awaiting_final_review"],
  draft: ["draft"],
};

export default function JobsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedJobId = searchParams.get("jobId") ?? undefined;
  const [tab, setTab] = useState("all");
  const [query, setQuery] = useState("");
  const [view, setView] = useState<"list" | "grid">("list");
  const { workspace, error: workspaceError } = useWorkspace();
  const jobsQuery = useCreationRecords(workspace?.id);
  const cancel = useCancelJob(workspace?.id);
  const retry = useRetryJob(workspace?.id);
  const confirmPlan = useConfirmPlan(workspace?.id);
  const jobs = useMemo(
    () =>
      (jobsQuery.data?.items ?? []).filter((job) =>
        (!tabFilters[tab] || tabFilters[tab]!.includes(job.status)) && `${job.display_name ?? ""} ${job.id}`.toLowerCase().includes(query.toLowerCase()),
      ),
    [jobsQuery.data, query, tab],
  );
  const allJobs = jobsQuery.data?.items ?? [];
  const actionError = cancel.error ?? retry.error ?? confirmPlan.error;
  const activeJobId = requestedJobId;
  const openJob = (id: string) => router.push(id.startsWith("rep_") ? `/style-replica?sessionId=${encodeURIComponent(id)}` : `/jobs?jobId=${encodeURIComponent(id)}`, { scroll: false });

  const runCancel = (job: Job | JobDetail) => {
    if (
      !allowsJobAction(job.allowed_actions, "cancel") ||
      !window.confirm("确认取消此任务？已经完成的任务不会回滚。")
    ) {
      return;
    }
    cancel.mutate({ jobId: job.id, allowedActions: job.allowed_actions });
  };

  const runRetry = (job: Job | JobDetail, itemId?: string) => {
    if (!allowsJobAction(job.allowed_actions, "retry")) return;
    const workspaceHref = jobWorkspaceHref(job);
    if (workspaceHref.startsWith("/style-replica?")) {
      router.push(workspaceHref);
      return;
    }
    retry.mutate({
      jobId: job.id,
      allowedActions: job.allowed_actions,
      scope: itemId ? "selected" : "failed",
      itemIds: itemId ? [itemId] : [],
    });
  };

  const runConfirmPlan = (job: JobDetail) => {
    if (!job.plan || !allowsJobAction(job.allowed_actions, "confirm_plan")) return;
    confirmPlan.mutate({
      jobId: job.id,
      revision: job.plan.revision,
      allowedActions: job.allowed_actions,
      approvalText: "我已检查并确认当前生成方案。",
    });
  };

  return (
    <AppShell>
      <div className="content-page">
        <div className="page-title-row">
          <div><span>创作管理</span><h1>任务中心</h1><p>任务结束后记录保留 30 天；草稿从创建起保留 30 天，请及时下载结果。</p></div>
        </div>
        <section className="job-stats">
          <div><span className="stat-icon stat-icon--violet"><Sparkles size={19} /></span><p>当前加载</p><strong>{jobsQuery.data?.total ?? allJobs.length}</strong><small>由任务列表统计</small></div>
          <div><span className="stat-icon stat-icon--green"><CheckCircle2 size={19} /></span><p>已完成</p><strong>{allJobs.filter((job) => job.status === "completed").length}</strong><small>已完成任务</small></div>
          <div><span className="stat-icon stat-icon--blue"><Clock3 size={19} /></span><p>处理中</p><strong>{allJobs.filter((job) => ["queued", "planning", "processing"].includes(job.status)).length}</strong><small>实时更新</small></div>
          <div><span className="stat-icon stat-icon--orange"><TimerReset size={19} /></span><p>需处理</p><strong>{allJobs.filter((job) => ["partial", "failed", "awaiting_confirmation", "awaiting_final_review"].includes(job.status)).length}</strong><small>可恢复或确认</small></div>
        </section>

        <section className="records-card" aria-busy={jobsQuery.isPending}>
          <div className="record-tabs">
            <div>
              {[
                ["all", "全部任务"],
                ["active", "进行中"],
                ["completed", "已完成"],
                ["attention", "需处理"],
                ["draft", "草稿"],
              ].map(([key, label]) => (
                <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>
              ))}
            </div>
            <div className="view-toggle">
              <button className={view === "list" ? "active" : ""} onClick={() => setView("list")} aria-label="列表视图"><List size={17} /></button>
              <button className={view === "grid" ? "active" : ""} onClick={() => setView("grid")} aria-label="网格视图"><LayoutGrid size={17} /></button>
            </div>
          </div>
          <div className="records-toolbar">
            <label className="search-field" htmlFor="job-search"><span className="visually-hidden">搜索任务</span><Search size={16} /><input id="job-search" placeholder="搜索任务名称或编号" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
            <span>{jobs.length} 个任务</span>
          </div>

          {(workspaceError || jobsQuery.error) && (
            <div className="api-state api-state--error" role="alert">
              <FileWarning size={24} /><div><strong>任务服务暂时不可用</strong><p>{errorMessage(workspaceError ?? jobsQuery.error)}</p></div>
              <button className="secondary-button" onClick={() => void jobsQuery.refetch()}>重试</button>
            </div>
          )}
          {actionError && <div className="api-inline-error" role="alert">{errorMessage(actionError)}</div>}
          {jobsQuery.isPending && <div className="api-state"><LoaderCircle className="spin" /><p>正在加载任务…</p></div>}
          {!jobsQuery.isPending && !jobsQuery.error && jobs.length === 0 && (
            <div className="empty-state"><Search size={28} /><h3>{query || tab !== "all" ? "没有匹配任务" : "开始你的第一次创作"}</h3><p>{query || tab !== "all" ? "换个条件再试，或查看全部任务。" : "上传商品图，完成后可在这里查看进度和下载结果。"}</p>{query || tab !== "all" ? <button className="secondary-button" onClick={() => { setQuery(""); setTab("all"); }}>清除筛选</button> : <Link className="primary-button" href="/#start">开始创作</Link>}</div>
          )}
          {jobs.length > 0 && (
            <div className={view === "grid" ? "records-grid" : "records-list"}>
              {jobs.map((job) => (
                <article
                  key={job.id}
                  className="record-row"
                  role="button"
                  tabIndex={0}
                  aria-label={`查看任务详情 ${jobTitle(job)}`}
                  onClick={() => openJob(job.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      openJob(job.id);
                    }
                  }}
                >
                  <JobRecordCover workspaceId={workspace?.id ?? ""} job={job} />
                  <div className="record-main">
                    <div><StatusBadge status={job.status} /><small>{job.id}</small></div>
                    <h3><Link href={jobWorkspaceHref(job)} onClick={(event) => event.stopPropagation()}>{jobTitle(job)}</Link></h3><p>{jobNote(job)}</p>
                    {["queued", "planning", "processing", "cancelling"].includes(job.status) && <div className="record-progress"><i style={{ width: `${job.progress}%` }} /><span>{job.progress}%</span></div>}
                  </div>
                  <div className="record-meta"><span><Clock3 size={14} />{formatDate(job.created_at)}</span><strong>{jobCount(job)}</strong><small>{job.history_expires_at ? `保留至 ${formatDate(job.history_expires_at)}` : "实时更新"}</small></div>
                  <div className="record-actions">
                    {allowsJobAction(job.allowed_actions, "retry") && <button aria-label={`重试任务 ${jobTitle(job)}`} title="重试失败项" className="emphasis" onClick={(event) => { event.stopPropagation(); runRetry(job); }} disabled={retry.isPending}><RotateCcw size={16} /></button>}
                    {allowsJobAction(job.allowed_actions, "cancel") && <button aria-label={`取消任务 ${jobTitle(job)}`} title="取消任务" onClick={(event) => { event.stopPropagation(); runCancel(job); }} disabled={cancel.isPending}><Ban size={16} /></button>}
                    <Link aria-label={`打开对应工作台 ${jobTitle(job)}`} title="打开对应工作台" className="row-open" href={jobWorkspaceHref(job)} onClick={(event) => event.stopPropagation()}><ArrowUpRight size={16} /></Link>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
      {workspace && activeJobId && (
        <JobDetailPanel
          workspaceId={workspace.id}
          jobId={activeJobId}
          onClose={() => {
            router.replace("/jobs", { scroll: false });
          }}
          onCancel={runCancel}
          onRetry={(job) => runRetry(job)}
          onRetryItem={(job, itemId) => runRetry(job, itemId)}
          onConfirmPlan={runConfirmPlan}
          cancelPending={cancel.isPending}
          retryPending={retry.isPending}
          confirmPending={confirmPlan.isPending}
        />
      )}
    </AppShell>
  );
}
