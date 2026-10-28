export type JobListFilters = {
  type?: string;
  statuses?: string[];
  cursor?: string;
  limit?: number;
  allPages?: boolean;
};

function normalize(filters: JobListFilters) {
  return {
    type: filters.type ?? null,
    statuses: [...(filters.statuses ?? [])].sort(),
    cursor: filters.cursor ?? null,
    limit: filters.limit ?? 20,
    allPages: filters.allPages ?? false,
  };
}

export const jobKeys = {
  all: (workspaceId: string) => ["jobs", workspaceId] as const,
  lists: (workspaceId: string) =>
    [...jobKeys.all(workspaceId), "list"] as const,
  list: (workspaceId: string, filters: JobListFilters) =>
    [...jobKeys.lists(workspaceId), normalize(filters)] as const,
  detail: (workspaceId: string, jobId: string) =>
    [...jobKeys.all(workspaceId), "detail", jobId] as const,
  activeSummary: (workspaceId: string) =>
    [...jobKeys.all(workspaceId), "active-summary"] as const,
  summary: (workspaceId: string, range: string) =>
    [...jobKeys.all(workspaceId), "summary", range] as const,
};
