"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { isLocalDevelopmentAuthEnabled, LOCAL_DEV_AUTH_USER_ID } from "@/lib/auth/local-dev";

export type ReplicaWelcomeState = {
  enabled: boolean;
  status: "available" | "in_progress" | "recoverable" | "used" | "expired" | "unavailable";
  remaining_tasks?: number;
  recovery_available?: boolean;
  session_id: string | null;
  expires_at: string | null;
  newcomer_available: boolean;
};

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const headers: Record<string, string> = {};
  if (isLocalDevelopmentAuthEnabled()) headers["X-User-Id"] = LOCAL_DEV_AUTH_USER_ID;
  const response = await fetch(path, { headers, credentials: "include", signal });
  if (!response.ok) throw new Error("权益暂时无法读取，请稍后重试。");
  return response.json();
}

export function usePublicReplicaWelcome() {
  const [data, setData] = useState<{ enabled: boolean }>();
  useEffect(() => {
    // A guest's initial session check clears the private query cache. Public
    // campaign state must survive that event without needing a page reload.
    const controller = new AbortController();
    const refresh = () => {
      void read<{ enabled: boolean }>("/api/v1/public/promotions/replica-welcome", controller.signal)
        .then(value => { if (!controller.signal.aborted) setData(value); })
        .catch(() => { if (!controller.signal.aborted) setData(undefined); });
    };
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, []);
  return { data };
}

export function useReplicaWelcome(workspaceId: string | undefined) {
  return useQuery({ queryKey: ["replica-welcome", workspaceId], enabled: Boolean(workspaceId), retry: false,
    refetchInterval: 5000,
    queryFn: ({ signal }) => read<ReplicaWelcomeState>(`/api/v1/workspaces/${workspaceId}/promotions/replica-welcome`, signal) });
}
