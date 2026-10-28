export type ReplicaProgressDocument = {
  id: string;
  status: string;
  updated_at?: string;
  payload: {
    analysis_attempt_id?: string;
    error_code?: string;
    replication_method?: string;
    analysis_revision_available?: boolean;
    segmentation_repair_available?: boolean;
    segmentation_diagnostics?: { coverage_valid: boolean; columns: { reference_index: number | null; split_count: number; inventory_count: number; reason?: string }[] };
    progress?: number;
    job_ids?: string[];
    submission_complete?: boolean;
    execution_progress?: { percentage: number };
    analysis_progress?: { stage: string; total: number; planned: number; reviewed: number; accepted: number; pending_corrections: number };
  };
};

const terminal = new Set(["completed", "partial", "failed", "cancelled"]);

export function replicaRetryNeedsReconciliation(job: {
  error_code?: string | null;
  items?: { error_code?: string | null }[];
} | undefined): boolean {
  const unknown = new Set(["PROVIDER_OUTCOME_UNKNOWN", "IMAGE_RESULT_DOWNLOAD_UNCONFIRMED"]);
  return unknown.has(job?.error_code ?? "") || Boolean(job?.items?.some(item => unknown.has(item.error_code ?? "")));
}

export function replicaPlanFailure(document: ReplicaProgressDocument): { title: string; message: string } | undefined {
  if (document.status !== "failed" || document.payload.error_code !== "REPLICA_PLAN_AUDIT_REJECTED") return undefined;
  const progress = document.payload.analysis_progress;
  if (progress && Number.isInteger(progress.total) && Number.isInteger(progress.accepted) && progress.total > 0 && progress.accepted >= 0 && progress.accepted < progress.total) {
    return {
      title: `还有 ${progress.total - progress.accepted} 屏方案待调整`,
      message: progress.accepted > 0
        ? `已通过的 ${progress.accepted} 屏方案和原始素材已保留。请先调整未通过的方案，再继续处理。`
        : "已完成的规划和原始素材已保留。请先调整未通过的方案，再继续处理。",
    };
  }
  if (document.payload.replication_method === "style" && !document.payload.analysis_revision_available) {
    return { title: "风格方案需要调整", message: "系统生成的风格方案尚未通过前置检查。商品图、参考图和商品信息已保留，可使用现有素材重新诊断。" };
  }
  return { title: "部分方案需要调整", message: "部分方案仍未通过校验，已完成的规划和素材已保留。请先调整未通过的方案，再继续处理。" };
}

export function replicaAnalysisMessage(document: ReplicaProgressDocument): string | undefined {
  const progress = document.payload.analysis_progress;
  if (!progress) return undefined;
  if (progress.total > 0) {
    if (progress.accepted === progress.total) return `${progress.total} 屏方案已确认，正在准备生成`;
    if (progress.stage === "review_plan") return `已确认 ${progress.accepted} / ${progress.total} 屏方案，正在处理剩余部分`;
    if (progress.stage === "plan") return `已规划 ${progress.planned} / ${progress.total} 屏${progress.pending_corrections ? "，正在调整剩余方案" : "，保留整套画面的顺序与风格"}`;
  }
  if (progress.stage === "facts" || progress.stage === "product") return "正在统一商品信息，供整套画面使用";
  return undefined;
}

export function replicaWorkflowStep(document: ReplicaProgressDocument | undefined, jobStatuses: Readonly<Record<string, string>>): number {
  if (!document) return 0;
  const jobs = document.payload.job_ids ?? [];
  if (jobs.length) return document.payload.submission_complete !== false && jobs.every(id => jobStatuses[id] === "completed") ? 5 : 4;
  return document.status === "awaiting_confirmation" ? 3 : document.status === "analysing" ? 1 : 0;
}

export function replicaOverallProgress(document: ReplicaProgressDocument): number {
  if (document.status === "completed") return 100;
  if (document.payload.execution_progress) return Math.min(99, Math.max(25, document.payload.execution_progress.percentage));
  if (document.payload.job_ids?.length || document.status === "awaiting_confirmation") return 25;
  return Math.min(24, Math.max(0, Math.round((document.payload.progress ?? 0) * .24)));
}

function progressTimestamp(value: string): number {
  // SQLite drops the UTC offset on database round trips. Treat these backend
  // ISO timestamps as UTC so a newer poll is not discarded in a UTC+8 browser.
  const utcValue = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(value)
    ? `${value}Z`
    : value;
  return Date.parse(utcValue);
}

/** Reject older poll replies while allowing explicit new attempts to start at zero. */
export function mergeReplicaProgress<T extends ReplicaProgressDocument>(current: T | undefined, incoming: T): T {
  if (!current || current.id !== incoming.id) return incoming;
  if (current.updated_at && incoming.updated_at && progressTimestamp(incoming.updated_at) < progressTimestamp(current.updated_at)) return current;
  if (current.payload.analysis_attempt_id !== incoming.payload.analysis_attempt_id) return incoming;
  if (JSON.stringify(current.payload.job_ids) !== JSON.stringify(incoming.payload.job_ids)) return incoming;
  if (terminal.has(current.status) && !terminal.has(incoming.status)) return current;
  const execution = incoming.payload.execution_progress;
  return {
    ...incoming,
    payload: {
      ...incoming.payload,
      progress: Math.max(current.payload.progress ?? 0, incoming.payload.progress ?? 0),
      ...(execution && { execution_progress: {
        ...execution,
        percentage: Math.max(current.payload.execution_progress?.percentage ?? 0, execution.percentage),
      } }),
    },
  };
}

export function replicaSegmentationFailure(document: ReplicaProgressDocument): { title: string; message: string } | undefined {
  if (document.status !== "failed" || document.payload.error_code !== "REPLICA_SEGMENTATION_INVALID") return undefined;
  const diagnostic = document.payload.segmentation_diagnostics;
  if (!diagnostic) return { title: "参考图拆屏需要校正", message: "参考图的屏数或边界尚未一致核对，原始素材已保留。" };
  if (!diagnostic.coverage_valid) return { title: "参考图范围需要校正", message: "参考图存在遗漏、重叠或裁切范围分歧。原始素材已保留，可重新校正拆屏。" };
  const details = diagnostic.columns.filter(c => Number.isInteger(c.reference_index)).map(c =>
    c.reason === "boundaries" ? `第 ${c.reference_index! + 1} 张参考图的分屏边界位置不一致`
      : `第 ${c.reference_index! + 1} 张参考图：拆屏 ${c.split_count} 屏，复核 ${c.inventory_count} 屏`);
  return { title: "参考图拆屏需要校正", message: `${details.join("；") || "分屏边界尚未一致核对"}。原始素材与分析记录已保留。` };
}
