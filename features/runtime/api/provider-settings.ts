"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";
import type { DevelopmentProviderSettingsUpdate } from "@/lib/api/types";
import { runtimeKeys } from "./runtime";

const providerSettingsKey = ["development-provider-settings"] as const;

export function useDevelopmentProviderSettings(enabled: boolean) {
  return useQuery({
    queryKey: providerSettingsKey,
    enabled,
    queryFn: async ({ signal }) => {
      const result = await apiClient.GET(
        "/api/v1/development/provider-settings",
        { signal },
      );
      return unwrapApiResponse(result);
    },
  });
}

export function useUpdateDevelopmentProviderSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: DevelopmentProviderSettingsUpdate) => {
      const result = await apiClient.POST(
        "/api/v1/development/provider-settings",
        { body },
      );
      return unwrapApiResponse(result);
    },
    onSuccess: async (data) => {
      queryClient.setQueryData(providerSettingsKey, data);
      await queryClient.invalidateQueries({ queryKey: runtimeKeys.status });
    },
  });
}
