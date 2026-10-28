"use client";

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";
import { useAuthSession } from "@/features/auth/api/session";

export const workspaceKeys = {
  all: ["workspaces"] as const,
};

const WORKSPACE_REQUEST_TIMEOUT_MS = 10_000;

export function useWorkspace() {
  const authSession = useAuthSession();
  const query = useQuery({
    enabled: authSession.data === true,
    queryKey: workspaceKeys.all,
    // A stalled API connection must become a visible error instead of keeping
    // AuthBoundary on "正在载入工作区" forever. The user can retry explicitly
    // after the local backend has been restarted or recovered.
    retry: false,
    queryFn: async ({ signal }) => {
      const timeoutController = new AbortController();
      const timeoutId = setTimeout(
        () => timeoutController.abort(),
        WORKSPACE_REQUEST_TIMEOUT_MS,
      );
      const forwardAbort = () => timeoutController.abort(signal.reason);
      signal.addEventListener("abort", forwardAbort, { once: true });

      try {
        const result = await apiClient.GET("/api/v1/workspaces", {
          signal: timeoutController.signal,
        });
        return unwrapApiResponse(result);
      } catch (error) {
        if (timeoutController.signal.aborted && !signal.aborted) {
          throw new Error("工作区服务响应超时，请检查本地 API 服务后重试。");
        }
        throw error;
      } finally {
        clearTimeout(timeoutId);
        signal.removeEventListener("abort", forwardAbort);
      }
    },
    staleTime: 5 * 60_000,
  });

  const workspace = query.isSuccess ? query.data[0] ?? null : undefined;

  return {
    ...query,
    workspace,
    isEmpty: query.isSuccess && query.data.length === 0,
    workspaceState: query.isPending
      ? ("loading" as const)
      : query.isError
        ? ("error" as const)
        : query.data.length === 0
          ? ("empty" as const)
          : ("ready" as const),
  };
}
