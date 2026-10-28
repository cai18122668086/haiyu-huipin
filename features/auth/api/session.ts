"use client";
import { useQuery } from "@tanstack/react-query";
import { sessionExists } from "./auth";
import { withAuthTimeout } from "@/lib/auth/request-timeout";

export const AUTH_SESSION_QUERY_KEY = ["auth", "session"] as const;

// 展示页不等待会话检查，确认已登录后才请求个人数据和内部运行状态。
export function useAuthSession() {
  return useQuery({
    queryKey: AUTH_SESSION_QUERY_KEY,
    queryFn: () => withAuthTimeout(sessionExists()),
    staleTime: 15_000,
    retry: false,
  });
}
