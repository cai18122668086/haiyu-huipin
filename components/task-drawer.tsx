"use client";

import Link from "next/link";
import { Ban, ChevronRight, LoaderCircle, RotateCcw, X } from "lucide-react";
import { useEffect, useRef, type RefObject } from "react";

import { useCancelJob, useRetryJob } from "@/features/jobs/api/jobs";
import { useCreationRecords } from "@/features/jobs/api/creation-records";
import { useRuntimeStatus } from "@/features/runtime/api/runtime";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { formatDate, jobCount, jobNote, jobTitle } from "@/lib/jobs/presentation";
import { allowsJobAction } from "@/lib/jobs/actions";
import { jobWorkspaceHref } from "@/lib/jobs/workspace-link";
import { StatusBadge } from "./status-badge";
import { useBodyScrollLock } from "@/lib/hooks/use-body-scroll-lock";
import { errorMessage } from "@/lib/api/request";

const stageLabels: Record<string, string> = {
  draft: "草稿",
  queued: "排队中",
  planning: "正在规划",
  awaiting_confirmation: "等待确认",
  awaiting_final_review: "等待整组确认",
  processing: "正在生成",
  generating: "正在生成",
  retry: "正在重试",
  retrying: "正在重试",
  reused: "已复用",
  partial: "部分完成",
  completed: "已完成",
  failed: "生成失败",
  planning_failed: "生成方案规划失败",
  cancelling: "正在取消",
  cancelled: "已取消",
};

function presentJobStage(stage: string | null | undefined, status: string) {
  return stageLabels[stage ?? ""] ?? stageLabels[status] ?? "处理中";
}

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export function TaskDrawer({
  open,
  onClose,
  triggerRef,
}: {
  open: boolean;
  onClose: () => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
}) {
  const drawerRef = useRef<HTMLElement>(null);
  useBodyScrollLock(open);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef(false);
  const { workspace, error: workspaceError } = useWorkspace();
  const jobs = useCreationRecords(workspace?.id);
  const runtime = useRuntimeStatus();
  const cancel = useCancelJob(workspace?.id);
  const retry = useRetryJob(workspace?.id);
  const allJobs = jobs.data?.items ?? [];
  const visibleJobs = allJobs.slice(0, 4);
  const { refetch: refetchJobs } = jobs;
  useEffect(() => {
    if (open) void refetchJobs();
  }, [open, refetchJobs]);
  const runCancel = (job: (typeof visibleJobs)[number]) => {
    if (!allowsJobAction(job.allowed_actions, "cancel")) return;
    cancel.mutate({ jobId: job.id, allowedActions: job.allowed_actions });
  };
  const runRetry = (job: (typeof visibleJobs)[number]) => {
    if (!allowsJobAction(job.allowed_actions, "retry")) return;
    retry.mutate({ jobId: job.id, allowedActions: job.allowed_actions });
  };

  useEffect(() => {
    if (open) {
      restoreFocusRef.current = true;
      const frame = requestAnimationFrame(() => closeButtonRef.current?.focus());
      return () => cancelAnimationFrame(frame);
    }
    if (restoreFocusRef.current) {
      restoreFocusRef.current = false;
      triggerRef.current?.focus();
    }
  }, [open, triggerRef]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !drawerRef.current) return;
      const focusable = Array.from(
        drawerRef.current.querySelectorAll<HTMLElement>(focusableSelector),
      ).filter((element) => !element.hasAttribute("disabled"));
      const first = focusable[0];
      const last = focusable.at(-1);
      if (!first || !last) {
        event.preventDefault();
        drawerRef.current.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      } else if (!drawerRef.current.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  return (
    <>
      {open && (
        <button
          className="drawer-overlay"
          onClick={onClose}
          aria-label="关闭任务抽屉"
          tabIndex={-1}
        />
      )}
      <aside
        ref={drawerRef}
        id="task-drawer"
        className={`task-drawer ${open ? "open" : ""}`}
        role="dialog"
        aria-modal={open ? true : undefined}
        aria-labelledby="task-drawer-title"
        aria-hidden={!open}
        inert={open ? undefined : true}
        tabIndex={-1}
      >
        <div className="drawer-head">
          <div><p>任务中心</p><h2 id="task-drawer-title">创作进度</h2></div>
          <button ref={closeButtonRef} className="icon-button" onClick={onClose} aria-label="关闭任务抽屉"><X size={19} /></button>
        </div>
        <div className="drawer-summary">
          <div><span className="live-dot" /><p>正在处理</p><strong>{allJobs.filter((job) => ["queued", "planning", "processing", "cancelling"].includes(job.status)).length}</strong></div>
          <div><p>已完成</p><strong>{allJobs.filter((job) => job.status === "completed").length}</strong></div>
          <div><p>需处理</p><strong className="danger-text">{allJobs.filter((job) => ["failed", "partial", "awaiting_confirmation", "awaiting_final_review"].includes(job.status)).length}</strong></div>
        </div>
        <div className="drawer-list">
          {(workspaceError || jobs.error || cancel.error || retry.error) && <div className="api-state api-state--error" role="alert"><p>{errorMessage(workspaceError ?? jobs.error ?? cancel.error ?? retry.error)}</p><button className="secondary-button" onClick={() => void jobs.refetch()}>重新加载任务</button></div>}
          {jobs.isPending && <div className="api-state"><LoaderCircle className="spin" /><p>加载任务中…</p></div>}
          {!workspaceError && !jobs.error && !jobs.isPending && visibleJobs.length === 0 && <div className="api-state"><p>暂无生成任务。</p></div>}
          {visibleJobs.map((job) => (
            <article key={job.id} className="drawer-job drawer-job--api">
              <div className="drawer-job__body">
                <div className="drawer-job__top"><StatusBadge status={job.status} /><small>{formatDate(job.created_at)}</small></div>
                <Link href={jobWorkspaceHref(job)} onClick={onClose} className="drawer-job__link"><strong>{jobTitle(job)}</strong><p>{job.type === "domestic_main_image" && job.status === "completed" && runtime.data?.capabilities.features?.domestic_main_image?.execution === "mock" ? "Mock 流程已结束，没有生成真实图片。" : jobNote(job)}</p></Link>
                {["processing", "queued", "planning"].includes(job.status) && <div className="mini-progress"><i style={{ width: `${job.progress}%` }} /></div>}
                <div className="drawer-job__meta">
                  <span>{job.type === "domestic_main_image" && job.status === "completed" && runtime.data?.capabilities.features?.domestic_main_image?.execution === "mock" ? "0 / 6 张图片" : jobCount(job)}</span><span>{job.type === "domestic_main_image" && job.status === "completed" && runtime.data?.capabilities.features?.domestic_main_image?.execution === "mock" ? "未生成图片" : presentJobStage(job.stage, job.status)}</span>
                  {allowsJobAction(job.allowed_actions, "cancel") && <button aria-label={`取消任务 ${jobTitle(job)}`} onClick={() => runCancel(job)}><Ban size={14} /></button>}
                  {allowsJobAction(job.allowed_actions, "retry") && <button aria-label={`重试失败项 ${jobTitle(job)}`} onClick={() => runRetry(job)}><RotateCcw size={14} /></button>}
                </div>
              </div>
            </article>
          ))}
        </div>
        <Link href="/jobs" className="drawer-footer-link" onClick={onClose}>查看全部生成记录<ChevronRight size={16} /></Link>
      </aside>
    </>
  );
}
