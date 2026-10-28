"use client";

import Link from "next/link";
import { ArrowRight, Clock3, ImagePlus, LoaderCircle, Sparkles } from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { ProgressRing } from "@/components/progress-ring";
import { StatusBadge } from "@/components/status-badge";
import { useCreationRecords } from "@/features/jobs/api/creation-records";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { formatDate, jobCount, jobNote, jobTitle } from "@/lib/jobs/presentation";
import { errorMessage } from "@/lib/api/request";
import { jobWorkspaceHref } from "@/lib/jobs/workspace-link";

export default function DashboardPage() {
  const { workspace, error: workspaceError } = useWorkspace();
  const jobs = useCreationRecords(workspace?.id);
  const items = jobs.data?.items ?? [];
  const completed = items.filter((job) => job.status === "completed").length;
  const active = items.filter((job) => ["queued", "planning", "processing"].includes(job.status)).length;

  return (
    <AppShell>
      <div className="content-page personal-dashboard">
        <div className="page-title-row">
          <div><span>个人工作台</span><h1>个人控制台</h1><p>查看近期任务进度，快速继续你的电商图片创作。</p></div>
          <Link href="/#tools" className="primary-button"><Sparkles size={16} />新建创作</Link>
        </div>
        <section className="overview-strip">
          <div><p>已加载任务</p><strong>{jobs.data?.total ?? items.length}</strong><span>当前任务列表</span></div>
          <div><p>已完成</p><strong>{completed}</strong><span>已完成任务</span></div>
          <div><p>正在处理</p><strong>{active}</strong><span>状态自动同步</span></div>
          <div className="overview-strip__progress"><ProgressRing value={items.length ? Math.round((completed / items.length) * 100) : 0} label="完成率" /><span><p>任务完成率</p><strong>{items.length ? `${Math.round((completed / items.length) * 100)}%` : "暂无任务"}</strong><small>根据当前任务列表统计</small></span></div>
        </section>
        <section className="dashboard-grid">
          <div className="recent-jobs-card">
            <div className="card-heading"><div><span>最近任务</span><h2>继续最近创作</h2></div><Link href="/jobs">查看全部<ArrowRight size={15} /></Link></div>
            {jobs.isPending && <div className="api-state"><LoaderCircle className="spin" /><p>加载任务中…</p></div>}
            {(workspaceError || jobs.error) && <div className="api-state api-state--error" role="alert"><p>{errorMessage(workspaceError ?? jobs.error)}</p><button className="secondary-button" onClick={() => void jobs.refetch()}>重新加载任务</button></div>}
            {!workspaceError && !jobs.error && !jobs.isPending && items.length === 0 && <div className="api-state"><p>暂无生成任务。</p></div>}
            <div className="recent-job-list">
              {items.slice(0, 3).map((job) => (
                <article key={job.id}>
                  <div className="api-job-cover"><Sparkles size={20} /></div>
                  <div><div className="recent-job__title"><strong>{jobTitle(job)}</strong><StatusBadge status={job.status} /></div><p>{jobNote(job)}</p><span><Clock3 size={13} />{formatDate(job.created_at)}<i>·</i>{jobCount(job)}</span></div>
                  <Link href={jobWorkspaceHref(job)} aria-label={`查看任务 ${jobTitle(job)}`}><ArrowRight size={16} /></Link>
                </article>
              ))}
            </div>
          </div>
          <div className="dashboard-shortcuts">
            <div className="card-heading"><div><span>快捷开始</span><h2>常用创作工具</h2></div><ImagePlus size={22} /></div>
            <Link href="/workspace/local-edit"><strong>一句话改图</strong><span>上传素材并进行局部精修</span><ArrowRight size={16} /></Link>
            <Link href="/workspace/detail-pack"><strong>商品详情图</strong><span>自动规划并生成商品详情套图</span><ArrowRight size={16} /></Link>
            <Link href="/workspace/domestic-main-image"><strong>国内电商主图</strong><span>生成 1 张搜索主图和 5 张连续轮播图</span><ArrowRight size={16} /></Link>
          </div>
        </section>
      </div>
    </AppShell>
  );
}
