"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";

export function useCreationSummary(workspaceId?: string) {
  return useQuery({
    queryKey: ["creation-summary", workspaceId],
    enabled: Boolean(workspaceId),
    queryFn: async ({ signal }) => unwrapApiResponse(await apiClient.GET(
      "/api/v1/workspaces/{workspace_id}/creation-summary",
      { params: { path: { workspace_id: workspaceId! } }, signal },
    )),
    refetchInterval: 5000,
  });
}
