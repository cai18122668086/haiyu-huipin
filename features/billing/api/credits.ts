"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";

export function useCreditAccount(workspaceId: string | undefined) {
  return useQuery({
    queryKey: ["credits", workspaceId],
    enabled: Boolean(workspaceId),
    queryFn: async ({ signal }) => unwrapApiResponse(await apiClient.GET(
      "/api/v1/workspaces/{workspace_id}/credits",
      { params: { path: { workspace_id: workspaceId! } }, signal },
    )),
    refetchInterval: 5000,
    retry: false,
  });
}

export function useCreditLedger(workspaceId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ["credits-ledger", workspaceId],
    enabled: enabled && Boolean(workspaceId),
    queryFn: async ({ signal }) => unwrapApiResponse(await apiClient.GET(
      "/api/v1/workspaces/{workspace_id}/credits/ledger",
      { params: { path: { workspace_id: workspaceId! }, query: { limit: 100 } }, signal },
    )),
    refetchInterval: 5000,
    retry: false,
  });
}
