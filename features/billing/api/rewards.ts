"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";

export const rewardKey = (workspaceId: string | undefined) => ["account-rewards", workspaceId] as const;

export function useRewards(workspaceId: string | undefined) {
  return useQuery({ queryKey: rewardKey(workspaceId), enabled: Boolean(workspaceId), retry: false,
    refetchInterval: 30_000,
    queryFn: async ({ signal }) => unwrapApiResponse(await apiClient.GET("/api/v1/workspaces/{workspace_id}/rewards", {
      params: { path: { workspace_id: workspaceId! } }, signal,
    })),
  });
}

export function useCheckin(workspaceId: string | undefined) {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrapApiResponse(await apiClient.POST("/api/v1/workspaces/{workspace_id}/rewards/check-in", {
      params: { path: { workspace_id: workspaceId! } },
    })),
    onSuccess: async result => {
      cache.setQueryData(rewardKey(workspaceId), result.rewards);
      await Promise.all([
        cache.invalidateQueries({ queryKey: ["credits", workspaceId] }),
        cache.invalidateQueries({ queryKey: ["credits-ledger", workspaceId] }),
        cache.invalidateQueries({ queryKey: ["credit-ledger-history", workspaceId] }),
        cache.invalidateQueries({ queryKey: ["credit-membership", workspaceId] }),
      ]);
    },
  });
}
