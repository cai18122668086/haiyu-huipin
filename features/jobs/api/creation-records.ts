"use client";

import { useCallback, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useJobs } from "./jobs";
import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";
import { mergeCreationRecords, type ReplicaCreationRecord } from "@/lib/jobs/creation-records";
import type { JobStatus } from "@/lib/api/types";

export function useCreationRecords(workspaceId: string | undefined, filters: { statuses?: JobStatus[]; limit?: number } = {}) {
  const jobs = useJobs(workspaceId, { limit: 100, allPages: true });
  const replicas = useQuery({
    queryKey: ["replica-creation-records", workspaceId],
    enabled: Boolean(workspaceId),
    queryFn: async ({ signal }): Promise<ReplicaCreationRecord[]> => {
      return unwrapApiResponse(await apiClient.GET("/api/v1/workspaces/{workspace_id}/replica/records", {
        params: { path: { workspace_id: workspaceId! } }, signal,
      }));
    },
    refetchInterval: 5000,
  });
  const { refetch } = jobs;
  const { refetch: refetchReplicas } = replicas;
  const known = new Set(jobs.data?.items.map(job => job.id));
  const oldest = jobs.data?.items.at(-1)?.created_at ?? "";
  const missingJobIds = [...new Set((replicas.data ?? [])
    .filter(record => record.created_at >= oldest)
    .flatMap(record => record.job_ids).filter(id => !known.has(id)))].sort().join(",");
  useEffect(() => {
    if (missingJobIds) void refetch();
  }, [missingJobIds, refetch]);
  const refetchRecords = useCallback(async () => { await Promise.all([refetch(), refetchReplicas()]); }, [refetch, refetchReplicas]);
  const merged = mergeCreationRecords(jobs.data?.items ?? [], replicas.data ?? [], workspaceId ?? "");
  const filtered = filters.statuses ? merged.filter(job => filters.statuses!.includes(job.status)) : merged;
  return {
    ...jobs,
    data: { items: filters.limit ? filtered.slice(0, filters.limit) : filtered, total: filtered.length, next_cursor: null, has_more: filters.limit ? filtered.length > filters.limit : false },
    isPending: jobs.isPending || replicas.isPending,
    error: jobs.error ?? replicas.error,
    refetch: refetchRecords,
  };
}
