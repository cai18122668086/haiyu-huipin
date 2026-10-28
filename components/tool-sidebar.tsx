"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowUpRight, CircleHelp, Clock3, FolderOpen, Grid2X2, Home, Lightbulb, Plus, X } from "lucide-react";
import { PRIMARY_CREATION_ENTRIES } from "@/lib/creation-entrypoints";
import { useJobs } from "@/features/jobs/api/jobs";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { formatDate, jobTitle } from "@/lib/jobs/presentation";
import { getFeatureByRoute } from "@/lib/feature-registry";
import { jobWorkspaceHref } from "@/lib/jobs/workspace-link";

export function ToolSidebar({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const pathname = usePathname();
  const { workspace } = useWorkspace();
  const currentFeature = getFeatureByRoute(pathname);
  const isWorkspace = Boolean(currentFeature);
  const jobs = useJobs(workspace?.id, {
    type: currentFeature?.jobType,
    limit: 4,
  });
  const recentJobs = jobs.data?.items ?? [];
  const createHref = currentFeature?.route ?? "/tools";

  return (
    <>
      {open && <button className="mobile-overlay" onClick={onClose} aria-label="关闭菜单" />}
      <aside className={`tool-sidebar ${open ? "mobile-open" : ""}`}>
        <div className="sidebar-mobile-head">
          <strong>功能导航</strong>
          <button className="icon-button" onClick={onClose} aria-label="关闭菜单">
            <X size={18} />
          </button>
        </div>
        <Link href={createHref} className="new-task-button" onClick={onClose}>
          <Plus size={17} />
          新建创作
        </Link>
        {isWorkspace ? (
          <>
            <section className="workspace-history">
              <p>最近创作</p>
              <div>
                {recentJobs.map((job) => (
                  <Link href={jobWorkspaceHref(job)} key={job.id} onClick={onClose}>
                    <span>{jobTitle(job)}</span>
                    <small>{formatDate(job.created_at)}</small>
                  </Link>
                ))}
              </div>
            </section>
            <div className="workspace-sidebar-links">
              <Link href="/jobs" onClick={onClose}>
                <Clock3 size={17} />
                生成记录
              </Link>
              <Link href="/assets" onClick={onClose}>
                <FolderOpen size={17} />
                个人素材库
              </Link>
            </div>
          </>
        ) : (
          <>
            <nav className="haiyu-sidebar-nav" aria-label="工作区导航">
              {[
                { href: "/", label: "创作首页", icon: Home },
                { href: "/tools", label: "全部工具", icon: Grid2X2 },
                { href: "/inspiration", label: "灵感专区", icon: Lightbulb },
                { href: "/help", label: "帮助中心", icon: CircleHelp },
                { href: "/assets", label: "我的素材", icon: FolderOpen },
                { href: "/jobs", label: "历史记录", icon: Clock3 },
              ].map(({ href, label, icon: Icon }) => <Link key={href} href={href} onClick={onClose} aria-current={pathname === href ? "page" : undefined}><Icon size={17} /><span>{label}</span></Link>)}
            </nav>
            <section className="haiyu-sidebar-primary"><p>优先体验</p>{PRIMARY_CREATION_ENTRIES.slice(0, 3).map(entry => <Link key={entry.id} href={entry.href} onClick={onClose}><span>{entry.label}</span><ArrowUpRight size={14} /></Link>)}</section>
            <div className="sidebar-tip">
              <span>HAIYU</span>
              <strong>让创作流程更顺畅</strong>
              <p>从商品原图，到心动视觉</p>
            </div>
          </>
        )}
      </aside>
    </>
  );
}

