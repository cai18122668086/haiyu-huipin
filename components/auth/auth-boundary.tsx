"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { isEmailVerified, sessionExists, signOut } from "@/features/auth/api/auth";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { initializeAuthClient } from "@/lib/auth/client";
import { getAuthMode } from "@/lib/auth/config";
import { currentReturnTo, sanitizeReturnTo, verifyEmailHref } from "@/lib/auth/return-to";
import { AUTH_ROUTES, isPublicRoute, routeRequiresWorkspace } from "@/lib/auth/routes";
import { onAuthSessionChanged, onAuthUnauthorised, onLoginRequested } from "@/lib/auth/session-events";
import { AUTH_SESSION_QUERY_KEY } from "@/features/auth/api/session";
import { withAuthTimeout } from "@/lib/auth/request-timeout";
import { AuthStatus } from "./auth-status";
import { AuthDialog } from "./auth-dialog";
import { AuthDiagnosticDetails } from "./auth-diagnostic-details";
import { beginAuthDiagnostics, createAuthDiagnostic, finishAuthDiagnostics, observeAuthCheck, reportAuthDiagnostic } from "@/lib/auth/diagnostics";
import { authFailureCanRetry, type AuthDiagnostic, type AuthPhase } from "@/lib/auth/diagnostic-payload";

type BoundaryResult = { state: "authenticated" } | { state: "unauthenticated" } | { state: "error"; diagnostic: AuthDiagnostic };

export function AuthBoundary({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [result, setResult] = useState<BoundaryResult>();
  const [retryKey, setRetryKey] = useState(0);
  const [recovering, setRecovering] = useState(false);
  const [sessionRevision, setSessionRevision] = useState(0);
  const [loginTarget, setLoginTarget] = useState<string | null>(null);
  // This opt-in is only supplied by an ignored local .env.local file so the help UI can be reviewed without an external auth service.
  // Production and normal local runs retain the existing route protection because this is false unless explicitly configured.
  const localHelpPreview = process.env.NEXT_PUBLIC_LOCAL_HELP_PREVIEW === "true" && pathname === "/help";
  const localHomePreview = process.env.NEXT_PUBLIC_LOCAL_HOME_PREVIEW === "true" && pathname === "/";
  const localInspirationPreview =
    process.env.NEXT_PUBLIC_LOCAL_INSPIRATION_PREVIEW === "true" && pathname === "/inspiration";
  const publicRoute =
    isPublicRoute(pathname) || localHelpPreview || localHomePreview || localInspirationPreview;
  const developmentAuth = getAuthMode() === "development";

  const redirectToLogin = useCallback(() => {
    queryClient.clear();
    setResult({ state: "unauthenticated" });
    setLoginTarget(currentReturnTo());
  }, [queryClient]);

  const enterFeature = useCallback(async (target: string) => {
    const returnTo = sanitizeReturnTo(target);
    if (developmentAuth || queryClient.getQueryData(AUTH_SESSION_QUERY_KEY) === true) {
      router.push(returnTo);
      return;
    }
    try {
      if (await withAuthTimeout(sessionExists())) { router.push(returnTo); return; }
    } catch { /* 会话服务暂不可用时仍允许浏览，登录框负责显示后续请求错误。 */ }
    setLoginTarget(returnTo);
  }, [developmentAuth, queryClient, router]);

  useEffect(() => onLoginRequested((target) => { void enterFeature(target); }), [enterFeature]);
  useEffect(() => {
    if (!publicRoute || developmentAuth) return;
    const interceptFeatureLink = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      const anchor = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>("a[href]") : null;
      if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
      const target = new URL(anchor.href, window.location.href);
      if (target.origin !== window.location.origin || isPublicRoute(target.pathname) || queryClient.getQueryData(AUTH_SESSION_QUERY_KEY) === true) return;
      event.preventDefault();
      event.stopPropagation();
      void enterFeature(`${target.pathname}${target.search}${target.hash}`);
    };
    window.addEventListener("click", interceptFeatureLink, true);
    return () => window.removeEventListener("click", interceptFeatureLink, true);
  }, [developmentAuth, publicRoute, queryClient, enterFeature]);

  useEffect(() => {
    if (!developmentAuth) initializeAuthClient();
  }, [developmentAuth]);
  useEffect(() => {
    if (developmentAuth) return;
    return onAuthUnauthorised(() => {
      queryClient.clear();
      setResult(undefined);
      if (!publicRoute) redirectToLogin();
    });
  }, [developmentAuth, publicRoute, redirectToLogin, queryClient]);
  useEffect(() => onAuthSessionChanged((authenticated) => {
    queryClient.clear();
    queryClient.setQueryData(AUTH_SESSION_QUERY_KEY, authenticated);
    if (authenticated) setLoginTarget(null);
    setResult(undefined);
    setSessionRevision((value) => value + 1);
  }), [queryClient]);
  useEffect(() => {
    if (publicRoute || developmentAuth) return;
    let cancelled = false;
    const diagnostics = beginAuthDiagnostics();
    async function checkAuthentication() {
      setRecovering(false);
      const started = performance.now();
      for (let attempt = 0; attempt < 2; attempt += 1) {
        let phase: AuthPhase = "session";
        try {
          if (!(await withAuthTimeout(observeAuthCheck(diagnostics, attempt + 1, "session", sessionExists)))) {
            if (!cancelled) redirectToLogin();
            return;
          }
          if (cancelled) return;
          phase = "email_verification";
          if (!(await withAuthTimeout(observeAuthCheck(diagnostics, attempt + 1, "email_verification", isEmailVerified)))) {
            if (!cancelled) { queryClient.clear(); router.replace(verifyEmailHref(currentReturnTo())); }
            return;
          }
          if (!cancelled) setResult({ state: "authenticated" });
          return;
        } catch (error) {
          if (cancelled) return;
          const diagnostic = createAuthDiagnostic(error, phase, started, diagnostics);
          if (attempt === 0 && authFailureCanRetry(diagnostic.reason, diagnostic.http_status ?? undefined)) {
            setRecovering(true);
            await new Promise((resolve) => setTimeout(resolve, 750));
            if (cancelled) return;
            continue;
          }
          reportAuthDiagnostic(diagnostic);
          setResult({ state: "error", diagnostic });
          return;
        }
      }
    }
    void checkAuthentication().finally(() => finishAuthDiagnostics(diagnostics));
    return () => { cancelled = true; finishAuthDiagnostics(diagnostics); };
  }, [developmentAuth, publicRoute, queryClient, redirectToLogin, retryKey, router, sessionRevision]);

  const loginDialog = loginTarget !== null ? <AuthDialog returnTo={loginTarget} onClose={() => {
    setLoginTarget(null);
    if (!publicRoute && result?.state === "unauthenticated") router.replace("/");
  }} /> : null;
  if (publicRoute) return <>{children}{loginDialog}</>;
  if (developmentAuth) return routeRequiresWorkspace(pathname) ? <WorkspaceGate>{children}</WorkspaceGate> : <>{children}</>;
  if (!result) return <AuthStatus busy title={recovering ? "正在恢复连接" : "正在确认登录状态"} description={recovering ? "连接暂时中断，正在为你重新检查。" : "请稍候，我们正在安全地恢复你的会话。"} />;
  if (result.state === "unauthenticated") return <><AuthStatus title="登录后开始创作" description="登录你的手机号账号，即可使用这项功能。"><button type="button" className="auth-button auth-button--primary" onClick={() => setLoginTarget(currentReturnTo())}>登录 / 注册</button><Link href="/">返回首页</Link></AuthStatus>{loginDialog}</>;
  if (result.state === "error") return <AuthStatus title="暂时无法确认登录状态" description={result.diagnostic.reason === "offline" ? "当前网络已断开，请连接网络后重新检查。" : "连接暂时未能恢复，请稍后重新检查。"}>
    <button className="auth-button auth-button--primary" onClick={() => { setResult(undefined); setRetryKey((key) => key + 1); }}>重新检查</button>
    <Link className="auth-button auth-button--secondary" href={AUTH_ROUTES.login}>返回登录</Link>
    <AuthDiagnosticDetails diagnostic={result.diagnostic} />
  </AuthStatus>;
  return <>{routeRequiresWorkspace(pathname) ? <WorkspaceGate>{children}</WorkspaceGate> : children}{loginDialog}</>;
}

function WorkspaceGate({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const workspaceQuery = useWorkspace();
  if (workspaceQuery.isPending) return <AuthStatus busy title="正在载入工作区" description="登录已完成，正在准备你的创作后台。" />;
  if (workspaceQuery.isError) return <AuthStatus title="工作区载入失败" description="暂时无法读取工作区，请稍后重试。登录状态不会受到影响。">
    <button className="auth-button auth-button--primary" onClick={() => void workspaceQuery.refetch()}>重新载入</button>
    <Link className="auth-button auth-button--secondary" href={AUTH_ROUTES.account}>前往账户</Link>
  </AuthStatus>;
  if (workspaceQuery.isEmpty) return <AuthStatus title="账号已就绪，等待分配工作区" description="工作区是登录后承载素材、任务和团队权限的创作后台。当前账号尚未加入工作区，请联系管理员完成分配。">
    <Link className="auth-button auth-button--primary" href={AUTH_ROUTES.account}>查看账户</Link>
    <button className="auth-button auth-button--secondary" onClick={async () => { await signOut(); queryClient.clear(); router.replace(AUTH_ROUTES.login); }}>退出登录</button>
  </AuthStatus>;
  return <>{children}</>;
}
