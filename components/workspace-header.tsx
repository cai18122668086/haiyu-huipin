"use client";
import { clearCreationDraft } from "@/lib/hooks/use-creation-draft";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { getFeatureByRoute } from "@/lib/feature-registry";
import Link from "next/link";
import { ChevronLeft, Plus } from "lucide-react";
import styles from "./workspace-header.module.css";

export function WorkspaceHeader({
  eyebrow,
  title,
  description,
  meta,
  jobId,
}: {
  eyebrow: string;
  title: string;
  description: string;
  meta?: React.ReactNode;
  jobId?: string;
}) {
  const { workspace } = useWorkspace();
  function startNewTask() {
    if (typeof window !== "undefined") {
      const feature = getFeatureByRoute(window.location.pathname);
      if (workspace && feature) clearCreationDraft(workspace.id, feature.id);
      window.location.replace(window.location.pathname);
    }
  }

  return (
    <div className="workspace-header">
      <div>
        <Link href="/" className="back-link">
          <ChevronLeft size={15} />
          工作台
        </Link>
        <p>{eyebrow}</p>
        <h1>{title}</h1>
        <span>{description}</span>
        {jobId && <div className={styles.taskIdentity}><span>任务编号：{jobId}</span><Link href={`/jobs?jobId=${encodeURIComponent(jobId)}`}>查看任务详情</Link></div>}
      </div>
      <div className="workspace-header__actions">
        {meta}
        <button type="button" className="secondary-button" onClick={startNewTask}>
          <Plus size={16} />
          新建任务
        </button>
      </div>
    </div>
  );
}

