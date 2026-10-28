"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { unwrapApiResponse } from "@/lib/api/request";
import { useAuthSession } from "./session";

export type CurrentAuthUser = { id: string; email?: string | null; display_name?: string | null; status?: string; email_verified?: boolean };
export const authUserKeys = { current: ["auth", "me"] as const };

export function useCurrentAuthUser() {
  const authSession = useAuthSession();
  return useQuery({
    enabled: authSession.data === true,
    queryKey: authUserKeys.current,
    queryFn: async ({ signal }): Promise<CurrentAuthUser> => {
      const result = await apiClient.GET("/api/v1/me", { signal });
      return unwrapApiResponse(result) as CurrentAuthUser;
    },
    staleTime: 5 * 60_000,
  });
}

export function authUserLabel(user: CurrentAuthUser | undefined): string {
  return user?.display_name?.trim() || user?.email?.trim() || "个人中心";
}
