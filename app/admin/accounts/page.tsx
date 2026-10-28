"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type WheelEvent } from "react";
import { Loader2, LogIn, Pause, Play, Plus, RefreshCw, ShieldCheck, Square } from "lucide-react";

import { AdminShell } from "@/components/admin/admin-shell";
import { getAdminAccess, getAdminAccounts, getUserWorkspaces, type AdminOperationalAccount, type UserWorkspace } from "@/features/admin/api/admin";
import { apiClient } from "@/lib/api/client";
import { errorDiagnostic, unwrapApiResponse, type ApiResult } from "@/lib/api/request";
import styles from "./page.module.css";

type Platform = "taobao" | "1688" | "jd" | "pinduoduo";
type LoginSessionInfo = { id?: string; status?: string } | null;
type LoginStatusResponse = { state?: string; message?: string; loginSession?: LoginSessionInfo };
type LoginApiEnvelope = { data?: LoginStatusResponse } & LoginStatusResponse;
type LocalWorker = { id: string; device_id: string; status: string; agent_version: string; capabilities: string[] };

const platforms: Array<{ value: Platform; label: string }> = [
  { value: "taobao", label: "淘宝 / 天猫" },
  { value: "1688", label: "1688" },
  { value: "jd", label: "京东" },
  { value: "pinduoduo", label: "拼多多" },
];

const accountStatusLabels: Record<string, string> = { active: "可用", paused: "已暂停", disabled: "已停用" };
const loginStateLabels: Record<string, string> = { unknown: "未确认", ready: "已登录", needs_login: "需要登录或验证" };

const LOCAL_WORKER_BRIDGE_URLS = [
  "http://localhost:8765/v1/enrollment",
  "http://127.0.0.1:8765/v1/enrollment",
];

function platformLabel(value: string) {
  return platforms.find((item) => item.value === value)?.label ?? value;
}

export default function AdminAccountsPage() {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [accounts, setAccounts] = useState<AdminOperationalAccount[]>([]);
  const [workspaces, setWorkspaces] = useState<UserWorkspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [platform, setPlatform] = useState<Platform>("taobao");
  const [accountKey, setAccountKey] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [workerId, setWorkerId] = useState("");
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [workers, setWorkers] = useState<LocalWorker[]>([]);
  const [workerDeviceId, setWorkerDeviceId] = useState("polaris-local-pc");
  const [enrollmentToken, setEnrollmentToken] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [activeLoginIds, setActiveLoginIds] = useState<Set<string>>(() => new Set());
  const [previewUrls, setPreviewUrls] = useState<Record<string, string>>({});
  const [loginSessionIds, setLoginSessionIds] = useState<Record<string, string>>({});
  const [streamStates, setStreamStates] = useState<Record<string, "connecting" | "connected" | "fallback">>({});
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const pointerStart = useRef<{ accountId: string; x: number; y: number } | null>(null);
  const streams = useRef<Record<string, WebSocket>>({});
  const streamSeq = useRef(0);
  const loginSyncChannel = useRef<BroadcastChannel | null>(null);
  const [loginSyncRevision, setLoginSyncRevision] = useState(0);

  function sendBrowserInput(accountId: string, action: Record<string, unknown>) {
    const stream = streams.current[accountId];
    if (stream?.readyState === WebSocket.OPEN) {
      stream.send(JSON.stringify({ type: "input", seq: ++streamSeq.current, action: { session_id: loginSessionIds[accountId], ...action } }));
      return;
    }
    void fetch("/api/ecommerce/session/input", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: accountId, session_id: loginSessionIds[accountId], ...action }),
    }).then(async (response) => {
      if (response.ok) return;
      const payload = await response.json().catch(() => null);
      setError(payload?.detail?.message || payload?.detail || "浏览器输入未送达，请刷新登录窗口。 ");
    }).catch(() => setError("浏览器输入未送达，请检查本机 Worker 是否在线。"));
  }

  function imagePoint(event: PointerEvent<HTMLImageElement>) {
    const image = event.currentTarget;
    const rect = image.getBoundingClientRect();
    return {
      x: Math.max(0, Math.round((event.clientX - rect.left) * (image.naturalWidth / rect.width))),
      y: Math.max(0, Math.round((event.clientY - rect.top) * (image.naturalHeight / rect.height))),
    };
  }

  function onPreviewPointerDown(accountId: string, event: PointerEvent<HTMLImageElement>) {
    const point = imagePoint(event);
    pointerStart.current = { accountId, ...point };
    event.currentTarget.parentElement?.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function onPreviewPointerUp(accountId: string, event: PointerEvent<HTMLImageElement>) {
    const start = pointerStart.current;
    pointerStart.current = null;
    if (!start || start.accountId !== accountId) return;
    const point = imagePoint(event);
    const moved = Math.hypot(point.x - start.x, point.y - start.y) > 6;
    sendBrowserInput(accountId, moved ? { type: "drag", fromX: start.x, fromY: start.y, toX: point.x, toY: point.y } : { type: "click", ...point });
  }

  function onPreviewKeyDown(accountId: string, event: KeyboardEvent<HTMLDivElement>) {
    event.preventDefault();
    if (event.key.length === 1) sendBrowserInput(accountId, { type: "text", text: event.key });
    else sendBrowserInput(accountId, { type: "key", key: event.key });
  }

  function onPreviewWheel(accountId: string, event: WheelEvent<HTMLDivElement>) {
    event.preventDefault();
    sendBrowserInput(accountId, { type: "wheel", deltaX: event.deltaX, deltaY: event.deltaY });
  }

  async function load(): Promise<boolean> {
    setLoading(true);
    setError("");
    try {
      const [access, accountResult, workspaceResult] = await Promise.all([
        getAdminAccess(),
        getAdminAccounts({ limit: 100 }),
        getUserWorkspaces(),
      ]);
      setAllowed(access.can_manage);
      setAccounts(accountResult.items);
      // Account creation is a write operation: viewers can inspect a
      // workspace but must never be offered it as a selectable target.
      const writableWorkspaces = workspaceResult.filter(
        (workspace) => workspace.role === "owner" || workspace.role === "admin",
      );
      setWorkspaces(writableWorkspaces);
      setWorkspaceId((current) =>
        current && writableWorkspaces.some((workspace) => workspace.id === current)
          ? current
          : writableWorkspaces[0]?.id || "",
      );
      return true;
    } catch (cause) {
      if (allowed === null) setAllowed(false);
      setError(errorDiagnostic(cause));
      return false;
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
    // `load` is intentionally called once on mount; it is defined in this page component.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    void fetch(`/api/ecommerce/workers?workspace_id=${encodeURIComponent(workspaceId)}`, { credentials: "include" })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => {
        if (cancelled) return;
        const next = Array.isArray(payload?.data) ? payload.data as LocalWorker[] : [];
        setWorkers(next);
        setWorkerId((current) => current && next.some((worker) => worker.id === current) ? current : next.find((worker) => worker.status === "online")?.id || "");
      })
      .catch(() => { if (!cancelled) setWorkers([]); });
    return () => { cancelled = true; };
  }, [workspaceId]);

  async function createAccount() {
    if (!workspaceId || !accountKey.trim()) {
      setError("请选择工作区并输入账号标识。");
      return;
    }
    setBusyId("new");
    setError("");
    setMessage("");
    try {
      const result = await apiClient.POST("/api/ecommerce/accounts", {
        body: {
          workspace_id: workspaceId,
          platform,
          account_key: accountKey.trim(),
          display_name: displayName.trim() || null,
          worker_id: workerId || null,
        },
      });
      unwrapApiResponse(result);
      setAccountKey("");
      setDisplayName("");
      setWorkerId("");
      setShowForm(false);
      const refreshed = await load();
      setMessage(
        refreshed
          ? "账号已添加。请在账号列表中按需启动受控登录。"
          : "账号可能已创建，但列表刷新失败，请稍后手动刷新。",
      );
    } catch (cause) {
      setError(errorDiagnostic(cause));
    } finally {
      setBusyId(null);
    }
  }

  async function updateStatus(account: AdminOperationalAccount) {
    const nextStatus = account.status === "active" ? "paused" : "active";
    setBusyId(account.id);
    setError("");
    try {
      const result = await apiClient.PATCH("/api/ecommerce/accounts/{account_id}", {
        params: { path: { account_id: account.id } },
        body: { status: nextStatus },
      });
      unwrapApiResponse(result);
      const refreshed = await load();
      setMessage(
        refreshed
          ? nextStatus === "active"
            ? "账号已恢复。"
            : "账号已暂停，不会被新任务选中。"
          : "状态可能已更新，但列表刷新失败，请稍后手动刷新。",
      );
    } catch (cause) {
      setError(errorDiagnostic(cause));
    } finally {
      setBusyId(null);
    }
  }

  async function removeAccount(account: AdminOperationalAccount) {
    if (!window.confirm(`确认移除 ${account.label}？历史记录会保留。`)) return;
    setBusyId(account.id); setError("");
    try {
      const response = await fetch(`/api/ecommerce/accounts/${encodeURIComponent(account.id)}`, { method: "DELETE", credentials: "include" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setMessage(`${account.label} 已移除`); await load();
    } catch (cause) { setError(errorDiagnostic(cause)); } finally { setBusyId(null); }
  }

  async function rebindAccount() {
    const account = accounts.find((item) => item.id === selectedAccountId);
    if (!account || !workerId) { setError("请选择账号和本机 Worker。"); return; }
    setBusyId(account.id); setError("");
    try { const result = await apiClient.PATCH("/api/ecommerce/accounts/{account_id}", { params: { path: { account_id: account.id } }, body: { worker_id: workerId } }); unwrapApiResponse(result); setMessage("账号已绑定本机 Worker"); await load(); } catch (cause) { setError(errorDiagnostic(cause)); } finally { setBusyId(null); }
  }

  async function createWorkerEnrollment() {
    if (!workspaceId || !workerDeviceId.trim()) {
      setError("请选择工作区并输入本机设备名称。");
      return;
    }
    setBusyId("worker-enroll"); setError(""); setMessage(""); setEnrollmentToken("");
    try {
      const response = await fetch("/api/ecommerce/workers/enrollments", {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: workspaceId, device_id: workerDeviceId.trim() }),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.error?.message || `HTTP ${response.status}`);
      const token = payload?.data?.enrollment_token;
      if (!token) throw new Error("服务器未返回注册令牌");
      setEnrollmentToken(token);
      let bridgeError: unknown = null;
      let delivered = false;
      for (const bridgeUrl of LOCAL_WORKER_BRIDGE_URLS) {
        try {
          await fetch(bridgeUrl, {
            method: "POST",
            mode: "no-cors",
            headers: { "Content-Type": "text/plain" },
            body: JSON.stringify({ enrollment_token: token, device_id: workerDeviceId.trim() }),
          });
          delivered = true;
          break;
        } catch (cause) {
          bridgeError = cause;
        }
      }
      if (!delivered) {
        setMessage("");
        const errorCode = bridgeError instanceof Error ? bridgeError.message : "";
        setError(errorCode === "WORKER_DEVICE_MISMATCH"
          ? "\u4ee4\u724c\u5df2\u751f\u6210\uff0c\u4f46\u8bbe\u5907\u540d\u79f0\u4e0e\u672c\u673a Worker \u4e0d\u4e00\u81f4\uff0c\u8bf7\u4f7f\u7528 polaris-local-pc\u3002"
          : "\u4ee4\u724c\u5df2\u751f\u6210\uff0c\u4f46\u672c\u673a Worker \u6865\u63a5\u7aef\u53e3\u4e0d\u53ef\u7528\uff0c\u8bf7\u786e\u8ba4\u672c\u673a Worker \u4efb\u52a1\u6b63\u5728\u8fd0\u884c\u3002\u4ee4\u724c\u4ecd\u53ef\u91cd\u65b0\u53d1\u9001\u3002");
      } else {
        setEnrollmentToken("");
        setMessage("\u4ee4\u724c\u5df2\u53d1\u9001\u5230\u672c\u673a Worker\uff0c\u6b63\u5728\u6ce8\u518c\u5e76\u5207\u6362\u4e3a\u5728\u7ebf\u3002");
        window.setTimeout(() => void load(), 3000);
      }
    } catch (cause) { setError(errorDiagnostic(cause)); }
    finally { setBusyId(null); }
  }

  function updateLoginActivity(accountId: string, data: LoginStatusResponse) {
    const isActive = data.loginSession?.status === "active";
    const sessionId = data.loginSession?.id;
    if (isActive && sessionId) {
      setLoginSessionIds((current) => current[accountId] === sessionId ? current : ({ ...current, [accountId]: sessionId }));
    }
    setActiveLoginIds((current) => {
      if (current.has(accountId) === isActive) return current;
      const next = new Set(current);
      if (isActive) {
        next.add(accountId);
      } else {
        next.delete(accountId);
      }
      return next;
    });
    if (!isActive) {
      setLoginSessionIds((current) => {
        if (!(accountId in current)) return current;
        const next = { ...current };
        delete next[accountId];
        return next;
      });
      setPreviewUrls((currentPreview) => {
        const previewUrl = currentPreview[accountId];
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        if (!previewUrl) return currentPreview;
        const nextPreview = { ...currentPreview };
        delete nextPreview[accountId];
        return nextPreview;
      });
      const loginState = data.state === "ready" ? "ready" :
        ["needs_login", "needs_relogin", "risk_controlled"].includes(data.state ?? "") ? "needs_login" : null;
      if (loginState) setAccounts((current) => current.some((account) => account.id === accountId && account.login_state !== loginState)
        ? current.map((account) => account.id === accountId ? { ...account, login_state: loginState } : account) : current);
    }
  }

  function notifyLoginStatusChanged(accountId: string) {
    const payload = { type: "login-status-changed", accountId, at: Date.now() };
    loginSyncChannel.current?.postMessage(payload);
    try {
      window.localStorage.setItem("polaris-login-status", JSON.stringify(payload));
    } catch {
      // Private browsing or blocked storage does not prevent BroadcastChannel sync.
    }
  }

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== "polaris-login-status" || !event.newValue) return;
      setLoginSyncRevision((revision) => revision + 1);
    };
    window.addEventListener("storage", onStorage);
    if (typeof BroadcastChannel !== "undefined") {
      const channel = new BroadcastChannel("polaris-ecommerce-login");
      loginSyncChannel.current = channel;
      channel.onmessage = (event: MessageEvent<{ type?: string }>) => {
        if (event.data?.type === "login-status-changed") setLoginSyncRevision((revision) => revision + 1);
      };
    }
    return () => {
      window.removeEventListener("storage", onStorage);
      loginSyncChannel.current?.close();
      loginSyncChannel.current = null;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    let timer: number | undefined;
    async function refreshPreviews() {
      await Promise.all(Array.from(activeLoginIds).map(async (accountId) => {
        const sessionId = loginSessionIds[accountId];
        if (!sessionId) return;
        try {
          const response = await fetch(`/api/ecommerce/session/preview?session_id=${encodeURIComponent(sessionId)}`, {
            credentials: "include",
            cache: "no-store",
            signal: controller.signal,
          });
          if (!response.ok || cancelled) return;
          const nextUrl = URL.createObjectURL(await response.blob());
          if (cancelled) {
            URL.revokeObjectURL(nextUrl);
            return;
          }
          setPreviewUrls((currentPreview) => {
            const previousUrl = currentPreview[accountId];
            if (previousUrl) URL.revokeObjectURL(previousUrl);
            return { ...currentPreview, [accountId]: nextUrl };
          });
        } catch {
          // The preview is intentionally best-effort while Chrome is starting.
        }
      }));
      if (!cancelled) timer = window.setTimeout(() => void refreshPreviews(), 500);
    }
    if (activeLoginIds.size > 0) void refreshPreviews();
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [activeLoginIds, loginSessionIds]);

  useEffect(() => {
    if (activeLoginIds.size === 0) return;
    let cancelled = false;
    const controller = new AbortController();
    let timer: number | undefined;
    async function refreshLoginStatuses() {
      await Promise.all(Array.from(activeLoginIds).map(async (accountId) => {
        const account = accounts.find((item) => item.id === accountId);
        if (!account) return;
        try {
          const response = await apiClient.GET("/api/ecommerce/session/status", {
            params: { query: { account_id: account.id, platform: account.platform } },
            signal: controller.signal,
          });
          if (!cancelled) updateLoginActivity(account.id, readLoginStatus(response));
        } catch {
          // Keep the active preview while a transient status request is unavailable.
        }
      }));
      if (!cancelled) timer = window.setTimeout(() => void refreshLoginStatuses(), 1500);
    }
    void refreshLoginStatuses();
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [activeLoginIds, accounts, loginSyncRevision]);

  useEffect(() => {
    const sockets = streams.current;
    for (const accountId of activeLoginIds) {
      const account = accounts.find((item) => item.id === accountId);
      if (account?.worker_id) {
        continue;
      }
      if (sockets[accountId]) continue;
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const sessionId = loginSessionIds[accountId];
      if (!sessionId) continue;
      const socket = new WebSocket(`${protocol}//${window.location.host}/api/ecommerce/session/stream?account_id=${encodeURIComponent(accountId)}&session_id=${encodeURIComponent(sessionId)}`);
      sockets[accountId] = socket;
      socket.onopen = () => setStreamStates((current) => ({ ...current, [accountId]: "connected" }));
      socket.onmessage = (event) => {
        try {
          const frame = JSON.parse(event.data) as { type?: string; data?: string; format?: string };
          if (frame.type !== "frame" || !frame.data) return;
          const bytes = Uint8Array.from(atob(frame.data), (value) => value.charCodeAt(0));
          const url = URL.createObjectURL(new Blob([bytes], { type: frame.format === "png" ? "image/png" : "image/jpeg" }));
          setPreviewUrls((current) => {
            const previous = current[accountId];
            if (previous) URL.revokeObjectURL(previous);
            return { ...current, [accountId]: url };
          });
        } catch {
          // Keep the last valid preview when a frame is malformed.
        }
      };
      socket.onerror = () => setStreamStates((current) => ({ ...current, [accountId]: "fallback" }));
      socket.onclose = () => {
        delete sockets[accountId];
        setStreamStates((current) => ({ ...current, [accountId]: "fallback" }));
        window.setTimeout(() => {
          if (activeLoginIds.has(accountId)) {
            setActiveLoginIds((current) => new Set(current));
          }
        }, 1000);
      };
    }
    for (const accountId of Object.keys(sockets)) {
      if (!activeLoginIds.has(accountId)) {
        sockets[accountId].close();
        delete sockets[accountId];
      }
    }
    return () => {
      for (const socket of Object.values(sockets)) socket.close();
      for (const accountId of Object.keys(sockets)) delete sockets[accountId];
    };
  }, [activeLoginIds, accounts, loginSessionIds]);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    async function restoreActiveSessions() {
      await Promise.all(accounts.map(async (account) => {
        try {
          const response = await apiClient.GET("/api/ecommerce/session/status", {
            params: { query: { account_id: account.id, platform: account.platform } },
            signal: controller.signal,
          });
          if (!cancelled) updateLoginActivity(account.id, readLoginStatus(response));
        } catch {
          // A status refresh is best-effort; the account list remains usable.
        }
      }));
    }
    if (accounts.length > 0) void restoreActiveSessions();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [accounts, loginSyncRevision]);

  function readLoginStatus(response: ApiResult<unknown>): LoginStatusResponse {
    const envelope = unwrapApiResponse(response) as LoginApiEnvelope;
    return envelope.data ?? envelope;
  }

  async function startLogin(account: AdminOperationalAccount) {
    setBusyId(account.id);
    setError("");
    setMessage("");
    try {
      if (account.active_lease_task_id) {
        setError("该账号当前被任务占用，请等待任务完成后再登录。");
        return;
      }
      const response = await apiClient.POST("/api/ecommerce/session/start", {
        body: { account_id: account.id, platform: account.platform as Platform },
      });
      const data = readLoginStatus(response);
      updateLoginActivity(account.id, data);
      setMessage(data.message || "登录流程已启动，请继续在受控运维通道中完成登录。");
      notifyLoginStatusChanged(account.id);
      await load();
    } catch (cause) {
      setError(errorDiagnostic(cause));
    } finally {
      setBusyId(null);
    }
  }

  async function cancelLogin(account: AdminOperationalAccount) {
    setBusyId(account.id);
    setError("");
    try {
      const response = await apiClient.POST("/api/ecommerce/session/cancel", { body: { account_id: account.id } });
      const data = readLoginStatus(response);
      updateLoginActivity(account.id, data);
      setMessage(data.message || "已请求取消该账号的登录流程。");
      notifyLoginStatusChanged(account.id);
      await load();
    } catch (cause) {
      setError(errorDiagnostic(cause));
    } finally {
      setBusyId(null);
    }
  }

  async function refreshLogin(account: AdminOperationalAccount) {
    setBusyId(account.id);
    setError("");
    try {
      const response = await apiClient.GET("/api/ecommerce/session/status", {
        params: { query: { account_id: account.id, platform: account.platform } },
      });
      const data = readLoginStatus(response);
      updateLoginActivity(account.id, data);
      setMessage(`${account.label}：${data.message || loginStateLabels[data.state || "unknown"] || data.state || "状态已刷新"}`);
      await load();
    } catch (cause) {
      setError(errorDiagnostic(cause));
    } finally {
      setBusyId(null);
    }
  }

  if (loading || allowed === null) {
    return <AdminShell><div className={styles.center}><Loader2 className="spin" size={24} /> 正在检查管理员账号池权限…</div></AdminShell>;
  }
  if (!allowed) {
    return <AdminShell><div className={styles.center}><ShieldCheck size={30} /><h1>暂无电商账号管理权限</h1><p>{error || "请使用有效的管理员身份登录。"}</p><button className={styles.secondary} type="button" onClick={() => void load()}><RefreshCw size={15} />重新载入</button></div></AdminShell>;
  }

  return <AdminShell>
    <main className={styles.page}>
      <header className={styles.header}>
        <div><span className={styles.eyebrow}><ShieldCheck size={14} /> ADMIN / ACCOUNTS</span><h1>电商账号池</h1><p>账号、登录会话和独立浏览器 Profile 只在管理员后台维护；普通爬虫用户只能提交商品链接。</p></div>
        <button className={styles.secondary} type="button" onClick={() => void load()} disabled={loading}><RefreshCw size={15} />刷新</button>
      </header>
      {message ? <p className={styles.success} role="status">{message}</p> : null}
      {error ? <p className={styles.error} role="alert">{error}</p> : null}
      <section className={styles.card}>
        <div className={styles.sectionHeader}><div><h2>账号列表</h2><p>{accounts.length} 个账号 · 不在前台展示账号标识、Profile 或租约路径。</p><small aria-live="polite">实时登录通道：{Object.values(streamStates).some((state) => state === "connected") ? "已连接" : "降级轮询"}</small></div><button className={styles.primary} type="button" onClick={() => setShowForm((value) => !value)}><Plus size={15} />添加账号</button></div>
        {showForm ? <div className={styles.form}>
          <label>工作区<select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}><option value="">请选择工作区</option>{workspaces.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.id}</option>)}</select></label>
          <label>平台<select value={platform} onChange={(event) => setPlatform(event.target.value as Platform)}>{platforms.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
          <label>账号标识<input value={accountKey} onChange={(event) => setAccountKey(event.target.value)} placeholder="手机号、会员名或内部备注" /></label>
          <label>显示名称<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="例如：淘宝主账号" /></label>
          <label>本机执行设备<select value={workerId} onChange={(event) => setWorkerId(event.target.value)}><option value="">不绑定（仅限服务器/旧流程）</option>{workers.map((worker) => <option key={worker.id} value={worker.id}>{worker.device_id} · {worker.status} · {worker.agent_version}</option>)}</select></label>
          <button className={styles.primary} type="button" onClick={() => void createAccount()} disabled={busyId === "new"}>{busyId === "new" ? "保存中…" : "保存账号"}</button>
        </div> : null}
        <div className={styles.form}><label>本机设备名称<input value={workerDeviceId} onChange={(event) => setWorkerDeviceId(event.target.value)} placeholder="例如：polaris-local-pc" /></label><button className={styles.secondary} type="button" onClick={() => void createWorkerEnrollment()} disabled={busyId === "worker-enroll"}>{busyId === "worker-enroll" ? "生成中…" : "注册本机 Worker"}</button>{enrollmentToken ? <label>一次性注册令牌<input readOnly value={enrollmentToken} onFocus={(event) => event.currentTarget.select()} /></label> : null}</div>
        <div className={styles.form}><label>选择账号<select value={selectedAccountId} onChange={(event) => setSelectedAccountId(event.target.value)}><option value="">请选择账号</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.label}</option>)}</select></label><label>本机 Worker<select value={workerId} onChange={(event) => setWorkerId(event.target.value)}><option value="">请选择在线 Worker</option>{workers.map((worker) => <option key={worker.id} value={worker.id}>{worker.device_id} · {worker.status}</option>)}</select></label><button className={styles.secondary} type="button" onClick={() => void rebindAccount()}>绑定本机 Worker</button>{selectedAccountId ? <button className={styles.secondary} type="button" onClick={() => void removeAccount(accounts.find((item) => item.id === selectedAccountId) as AdminOperationalAccount)}>删除账号</button> : null}</div>
        <div className={styles.tableWrap}><table><thead><tr><th>账号</th><th>状态</th><th>任务与租约</th><th>登录运维</th></tr></thead><tbody>{accounts.length === 0 ? <tr><td colSpan={4} className={styles.empty}>暂无账号，请先添加一个平台账号。</td></tr> : accounts.map((account) => <tr key={account.id}><td><strong>{account.label}</strong><small>{platformLabel(account.platform)} · {account.workspace_name || account.workspace_id}</small></td><td><strong>{accountStatusLabels[account.status] || account.status}</strong><small>{loginStateLabels[account.login_state] || account.login_state}</small></td><td><strong>{account.profile_busy ? "使用中" : "空闲"}</strong><small>{account.active_lease_task_id || `${account.task_count} 个任务`}</small></td><td>{previewUrls[account.id] ? <div style={{ marginBottom: 8 }}><div tabIndex={0} role="application" aria-label={`${account.label}可操作登录浏览器`} onKeyDown={(event) => onPreviewKeyDown(account.id, event)} onWheel={(event) => onPreviewWheel(account.id, event)} style={{ width: "min(960px, 70vw)", maxHeight: "720px", overflow: "auto", border: "1px solid #d9d9e8", borderRadius: 8, background: "#fff", cursor: "crosshair", outline: "none" }}><img src={previewUrls[account.id]} alt={`${account.label}登录浏览器`} draggable={false} onPointerDown={(event) => onPreviewPointerDown(account.id, event)} onPointerUp={(event) => onPreviewPointerUp(account.id, event)} style={{ display: "block", width: "100%", height: "auto", userSelect: "none" }} /></div><small>可点击、拖动、滚动或先点击界面后输入；操作会发送到该账号的隔离登录浏览器</small></div> : null}<div className={styles.actions}><button type="button" className={styles.action} onClick={() => void refreshLogin(account)} disabled={busyId === account.id}><RefreshCw size={14} />状态</button>{activeLoginIds.has(account.id) ? null : <button type="button" className={styles.action} onClick={() => void startLogin(account)} disabled={busyId === account.id}><LogIn size={14} />{account.login_state === "ready" ? "重新登录" : "登录"}</button>}<button type="button" className={styles.action} onClick={() => void updateStatus(account)} disabled={busyId === account.id || account.status === "disabled"}>{account.status === "active" ? <Pause size={14} /> : <Play size={14} />}{account.status === "active" ? "暂停" : "恢复"}</button>{activeLoginIds.has(account.id) ? <button type="button" className={styles.action} onClick={() => void cancelLogin(account)} disabled={busyId === account.id}><Square size={14} />取消登录</button> : null}</div></td></tr>)}</tbody></table></div>
      </section>
    </main>
  </AdminShell>;
}

