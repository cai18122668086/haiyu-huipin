import { FEATURE_REGISTRY } from "@/lib/feature-registry";
import type { Job, JobDetail } from "@/lib/api/types";
import { resolveJobWorkspaceHref } from "@/lib/jobs/workspace-link-resolution";

export function jobWorkspaceHref(
  job: Pick<Job, "id" | "type" | "task_preset"> & Partial<Pick<JobDetail, "configuration" | "display_name">>,
): string {
  return resolveJobWorkspaceHref(job, FEATURE_REGISTRY);
}
