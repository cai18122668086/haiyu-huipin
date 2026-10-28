import type { Job, JobStatus } from "../api/types";

export type ReplicaCreationRecord = {
  id: string;
  status: string;
  method: "exact" | "style" | null;
  created_at: string;
  updated_at: string;
  job_ids: string[];
  current_job_ids?: string[];
  progress: number;
  stage: string | null;
  expected: number;
  generated: number;
};

export function mergeCreationRecords(jobs: Job[], replicas: ReplicaCreationRecord[], workspaceId: string): Job[] {
  const byId = new Map(jobs.map(job => [job.id, job]));
  const replicaJobs = new Set(replicas.flatMap(record => record.job_ids));
  const statuses: Record<string, JobStatus> = { analysing: "planning", generating: "processing", awaiting_confirmation: "planning", completed: "completed", failed: "failed", partial: "partial", cancelled: "cancelled" };
  const records: Job[] = replicas.map(record => {
    const job = (record.current_job_ids ?? record.job_ids).map(id => byId.get(id)).find(Boolean);
    const status = record.current_job_ids ? statuses[record.status] ?? "planning" : job?.status ?? statuses[record.status] ?? "planning";
    return {
      id: record.id, workspace_id: workspaceId, type: "reference_remix",
      display_name: `一键复刻 · ${record.method === "style" ? "风格复刻" : "一比一模仿"}`,
      created_at: record.created_at, updated_at: job?.updated_at ?? record.updated_at,
      status,
      stage: job?.stage ?? record.stage, progress: record.current_job_ids ? record.progress : job?.progress ?? record.progress,
      expected_item_count: record.expected || job?.expected_item_count || 0,
      succeeded_item_count: status === "completed" ? record.expected : job?.succeeded_item_count ?? 0,
      failed_item_count: job?.failed_item_count ?? 0,
      output_asset_count: job?.output_asset_count ?? 0,
      primary_output_asset_id: job?.primary_output_asset_id ?? null,
      history_expires_at: job?.history_expires_at ?? null,
      allowed_actions: [],
      cost: job?.cost ?? { actual: null, actual_credits: null, estimated_credits: null, estimated_max: null, estimated_min: null, usage_completeness: "none" },
    };
  });
  return [...jobs.filter(job => !replicaJobs.has(job.id)), ...records]
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
}
