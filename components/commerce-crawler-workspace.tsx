"use client";

import Link from "next/link";
import { formatBeijingDate } from "@/lib/datetime.mjs";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  CircleDot,
  Clock3,
  ExternalLink,
  Image as ImageIcon,
  LoaderCircle,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Square,
  Store,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";

import { getAssetAccessUrl } from "@/features/assets/api/assets";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { errorMessage, unwrapApiResponse } from "@/lib/api/request";
import { apiClient } from "@/lib/api/client";
import styles from "./commerce-crawler-workspace.module.css";

type Platform = "auto" | "1688" | "taobao" | "jd" | "pinduoduo";
type CrawlStatus =
  | "created"
  | "queued"
  | "running"
  | "waiting_session"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "retry_scheduled";

type CrawlTask = {
  id: string;
  workspace_id: string;
  platform: string;
  source_url: string;
  canonical_url: string;
  status: CrawlStatus;
  stage: string | null;
  progress: number;
  error_code: string | null;
  error_message_safe: string | null;
  attempt_count: number;
  max_attempts: number;
  result_snapshot_id: string | null;
  created_at: string | null;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
};

type CrawlEvent = {
  id: string;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  message: string;
  details: Record<string, unknown> | null;
  created_at: string | null;
};

type CrawlResult = {
  platform?: string;
  numIid?: string | null;
  title?: string | null;
  price?: string | null;
  shopName?: string | null;
  sales?: string | null;
  warnings?: string[];
  snapshotId?: string | null;
  assetIds?: Record<string, string[]>;
  assets?: Array<{ asset_id: string; role: string; ordinal: number; source_url?: string }>;
  imageUrls?: string[];
  descImageUrls?: string[];
  skuImageUrls?: string[];
  skuPropGroups?: Array<{ name: string; values: string[]; image_urls?: Record<string, string> }>;
};

type ApiEnvelope<T> = { success?: boolean; data: T };
type CrawlTaskHistory = {
  items: CrawlTask[];
  pagination: { total: number; limit: number; offset: number; has_more: boolean };
};

const CREATE_TASK_TIMEOUT_MS = 15_000;
const DETAIL_REQUEST_TIMEOUT_MS = 10_000;
const terminalStatuses = new Set<CrawlStatus>(["succeeded", "failed", "cancelled"]);

const platformLabels: Record<Exclude<Platform, "auto">, string> = {
  "1688": "1688",
  taobao: "淘宝 / 天猫",
  jd: "京东",
  pinduoduo: "拼多多",
};

const platformOptions: Array<{ value: Platform; label: string }> = [
  { value: "auto", label: "自动识别" },
  { value: "1688", label: "1688" },
  { value: "taobao", label: "淘宝 / 天猫" },
  { value: "jd", label: "京东" },
  { value: "pinduoduo", label: "拼多多" },
];

const statusLabels: Record<CrawlStatus, string> = {
  created: "已创建",
  queued: "排队中",
  running: "抓取中",
  waiting_session: "等待登录 / 验证",
  cancelling: "正在取消",
  succeeded: "抓取成功",
  failed: "抓取失败",
  cancelled: "已取消",
  retry_scheduled: "准备重试",
};

const statusTone: Record<CrawlStatus, string> = {
  created: "muted",
  queued: "blue",
  running: "blue",
  waiting_session: "amber",
  cancelling: "amber",
  succeeded: "green",
  failed: "red",
  cancelled: "muted",
  retry_scheduled: "blue",
};

function unwrap<T>(result: unknown): T {
  return unwrapApiResponse(result as { data?: T; error?: unknown; response: Response });
}

function formatTime(value: string | null | undefined) {
  if (!value) return "—";
  return formatBeijingDate(value, { dateStyle: "short", timeStyle: "medium" });
}

function formatPlatform(value: string | null | undefined) {
  if (!value) return "—";
  return platformLabels[value as Exclude<Platform, "auto">] ?? value;
}

function classifyUrl(raw: string): { platform: Exclude<Platform, "auto">; label: string } | null {
  try {
    const host = new URL(raw.trim()).hostname.toLowerCase().replace(/\.$/, "");
    const matches = (domain: string) => host === domain || host.endsWith(`.${domain}`);
    if (matches("1688.com")) return { platform: "1688", label: "1688" };
    if (matches("taobao.com") || matches("tmall.com") || matches("tb.cn")) {
      return { platform: "taobao", label: matches("tmall.com") ? "天猫" : "淘宝" };
    }
    if (matches("jd.com") || matches("3.cn") || matches("npcitem.jd.hk")) return { platform: "jd", label: "京东" };
    if (matches("pinduoduo.com") || matches("yangkeduo.com")) return { platform: "pinduoduo", label: "拼多多" };
    return null;
  } catch {
    return null;
  }
}

function eventIcon(event: CrawlEvent) {
  if (event.to_status === "succeeded") return <CheckCircle2 size={15} />;
  if (event.to_status === "failed") return <XCircle size={15} />;
  if (event.event_type.includes("cancel")) return <Square size={13} />;
  return <CircleDot size={15} />;
}

function AssetThumb({ workspaceId, assetId, label }: { workspaceId: string; assetId: string; label: string }) {
  const anchor = useRef<HTMLAnchorElement>(null);
  const [url, setUrl] = useState<string>();
  const [originalUrl, setOriginalUrl] = useState<string>();
  const [busy, setBusy] = useState(true);
  useEffect(() => {
    let active = true;
    let started = false;
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      if (started) return;
      started = true;
      timeout = setTimeout(() => controller.abort(), 12000);
      void getAssetAccessUrl(workspaceId, assetId, "inline", controller.signal, "thumbnail")
        .then((access) => { if (active) { setUrl(access.url); setOriginalUrl(access.original_url ?? access.url); } })
        .catch(() => { if (active) setUrl(undefined); })
        .finally(() => { clearTimeout(timeout); if (active) setBusy(false); });
    };
    // A result may contain dozens of detail/SKU images. Request signed URLs
    // only near the viewport instead of flooding auth and storage on mount.
    const observer = typeof IntersectionObserver === "undefined" ? undefined : new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        observer?.disconnect();
        load();
      }
    }, { rootMargin: "200px" });
    if (observer && anchor.current) observer.observe(anchor.current);
    else load();
    return () => { active = false; observer?.disconnect(); clearTimeout(timeout); controller.abort(); };
  }, [assetId, workspaceId]);
  return (
    <a ref={anchor} className={styles.assetCard} href={originalUrl} target="_blank" rel="noreferrer" aria-label={`查看${label}`}>
      <div className={styles.assetMedia}>
        {busy ? <LoaderCircle className="spin" size={20} /> : url ? <img src={url} alt={label} loading="lazy" decoding="async" /> : <ImageIcon size={22} />}
      </div>
      <span>{label}</span>
      <small>{assetId}</small>
    </a>
  );
}

export function CommerceCrawlerWorkspace() {
  const { workspace } = useWorkspace();
  const workspaceId = workspace?.id;
  const [url, setUrl] = useState("");
  const [platform, setPlatform] = useState<Platform>("auto");
  const [includeDescImages, setIncludeDescImages] = useState(true);
  const [maxAttempts, setMaxAttempts] = useState(1);
  const [tasks, setTasks] = useState<CrawlTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string>();
  const [task, setTask] = useState<CrawlTask>();
  const [events, setEvents] = useState<CrawlEvent[]>([]);
  const [result, setResult] = useState<CrawlResult>();
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [message, setMessage] = useState<string>();
  const [error, setError] = useState<string>();
  const detailRequestInFlight = useRef(false);

  const urlHint = useMemo(() => classifyUrl(url), [url]);
  const loadTaskHistory = useCallback(async () => {
    if (!workspaceId) return;
    try {
      const response = await apiClient.GET("/api/ecommerce/crawl-tasks", {
        params: { query: { workspace_id: workspaceId, limit: 8, offset: 0 } },
      });
      const history = unwrap<ApiEnvelope<CrawlTaskHistory>>(response).data;
      const nextTasks = history.items ?? [];
      setTasks(nextTasks);
      setSelectedTaskId(nextTasks[0]?.id);
      setTask(undefined);
      setEvents([]);
      setResult(undefined);
    } catch (requestError) {
      setError(errorMessage(requestError));
    }
  }, [workspaceId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadTaskHistory(), 0);
    return () => window.clearTimeout(timer);
  }, [loadTaskHistory]);

  const loadTaskDetail = useCallback(async (taskId: string, quiet = false) => {
    if (detailRequestInFlight.current) return;
    detailRequestInFlight.current = true;
    if (!quiet) setDetailLoading(true);
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), DETAIL_REQUEST_TIMEOUT_MS);
    try {
      const [taskResponse, eventsResponse] = await Promise.all([
        apiClient.GET("/api/ecommerce/crawl-tasks/{task_id}", { signal: controller.signal, params: { path: { task_id: taskId } } }),
        apiClient.GET("/api/ecommerce/crawl-tasks/{task_id}/events", { signal: controller.signal, params: { path: { task_id: taskId } } }),
      ]);
      const nextTask = unwrap<ApiEnvelope<CrawlTask>>(taskResponse).data;
      setTask(nextTask);
      setEvents(unwrap<ApiEnvelope<CrawlEvent[]>>(eventsResponse).data ?? []);
      setTasks((current) => current.some((item) => item.id === nextTask.id) ? current.map((item) => item.id === nextTask.id ? nextTask : item) : [nextTask, ...current].slice(0, 8));
      if (nextTask.status === "succeeded") {
        const resultResponse = await apiClient.GET("/api/ecommerce/crawl-tasks/{task_id}/result", { signal: controller.signal, params: { path: { task_id: taskId } } });
        setResult(unwrap<ApiEnvelope<{ result: CrawlResult }>>(resultResponse).data.result);
      } else {
        setResult(undefined);
      }
      return nextTask.status;
    } catch (requestError) {
      if (!quiet) setError(errorMessage(requestError));
    } finally {
      window.clearTimeout(timeoutId);
      if (!quiet) setDetailLoading(false);
      detailRequestInFlight.current = false;
    }
  }, []);

  useEffect(() => {
    if (!selectedTaskId) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async (quiet = false) => {
      const status = await loadTaskDetail(selectedTaskId, quiet);
      // Schedule only after the preceding request finishes. Completed tasks
      // no longer need to call the authentication service every 2.5 seconds.
      if (!cancelled && (!status || !terminalStatuses.has(status))) {
        // A hidden tab cannot display progress, so let it back off while
        // keeping visible tabs responsive. Returning to the tab triggers an
        // immediate refresh below.
        const delay = document.visibilityState === "hidden" ? 15_000 : 2_500;
        timer = window.setTimeout(() => void poll(true), delay);
      }
    };
    const refreshOnVisibility = () => {
      if (document.visibilityState !== "visible" || cancelled) return;
      if (timer !== undefined) window.clearTimeout(timer);
      void poll(true);
    };
    document.addEventListener("visibilitychange", refreshOnVisibility);
    timer = window.setTimeout(() => void poll(), 0);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", refreshOnVisibility);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [loadTaskDetail, selectedTaskId]);

  const createTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedUrl = url.trim();
    if (!workspace?.id) return setError("工作区尚未就绪，请稍后重试。");
    if (!trimmedUrl) return setError("请输入真实商品链接。");
    try { new URL(trimmedUrl); } catch { return setError("商品链接格式不正确，请粘贴完整 URL。"); }
    setLoading(true); setError(undefined); setMessage(undefined);
    const timeoutController = new AbortController();
    const timeoutId = window.setTimeout(
      () => timeoutController.abort(),
      CREATE_TASK_TIMEOUT_MS,
    );
    try {
      const response = await apiClient.POST("/api/ecommerce/crawl-tasks", {
        signal: timeoutController.signal,
        params: { header: { "Idempotency-Key": crypto.randomUUID() } },
        body: {
          workspace_id: workspace.id,
          url: trimmedUrl,
          platform,
          includeDescImages,
          max_attempts: maxAttempts,
          account_id: null,
        },
      });
      const created = unwrap<ApiEnvelope<CrawlTask>>(response).data;
      setTasks((current) => [created, ...current.filter((item) => item.id !== created.id)].slice(0, 8));
      setSelectedTaskId(created.id);
      setTask(created);
      setEvents([]);
      setResult(undefined);
      setMessage("任务已进入真实异步爬虫队列。系统会自动选择当前可用的账号池。");
    } catch (requestError) {
      if (timeoutController.signal.aborted) {
        // The API commits the task before dispatching it. If the auth/proxy
        // path stalls after that commit, recover the durable history instead
        // of leaving the submit button disabled forever.
        void loadTaskHistory();
        setError("提交响应超时，已刷新任务历史；请先确认是否已创建任务，再决定是否重试。");
      } else {
        setError(errorMessage(requestError));
      }
    } finally {
      window.clearTimeout(timeoutId);
      setLoading(false);
    }
  };

  const cancelTask = async () => {
    if (!task) return;
    setLoading(true); setError(undefined);
    try {
      const response = await apiClient.POST("/api/ecommerce/crawl-tasks/{task_id}/cancel", { params: { path: { task_id: task.id } } });
      const nextTask = unwrap<ApiEnvelope<CrawlTask>>(response).data;
      setTask(nextTask); setTasks((current) => current.map((item) => item.id === nextTask.id ? nextTask : item));
      setMessage("已发送取消请求。");
    } catch (requestError) { setError(errorMessage(requestError)); } finally { setLoading(false); }
  };

  const retryTask = async () => {
    if (!task) return;
    setLoading(true); setError(undefined);
    try {
      const response = await apiClient.POST("/api/ecommerce/crawl-tasks/{task_id}/retry", { params: { path: { task_id: task.id } } });
      const nextTask = unwrap<ApiEnvelope<CrawlTask>>(response).data;
      setTask(nextTask); setResult(undefined); setTasks((current) => current.map((item) => item.id === nextTask.id ? nextTask : item));
      setMessage("任务已重新排队。");
    } catch (requestError) { setError(errorMessage(requestError)); } finally { setLoading(false); }
  };

  const canCancel = task && ["created", "queued", "running", "waiting_session", "cancelling"].includes(task.status);
  const canRetry = task && ["failed", "waiting_session", "cancelled"].includes(task.status);
  const assetGroups = result?.assetIds ?? {};
  const assets = Object.entries(assetGroups).flatMap(([role, ids]) => ids.map((id) => ({ role, id })));

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <span className={styles.eyebrow}><Store size={16} /> 国内电商运营</span>
          <h1>真实商品爬虫</h1>
          <p>提交商品链接后进入异步任务队列，实时查看平台会话、抓取进度、事件和商品资产。</p>
        </div>
        <div className={styles.heroNote}><ShieldCheck size={18} /><span>真实 Provider 链路<br /><small>不返回 Cookie、Profile 路径或 Secret</small></span></div>
      </header>

      <div className={styles.layout}>
        <main className={styles.mainColumn}>
          <section className={styles.card}>
            <div className={styles.cardHeading}><div><span className={styles.step}>01</span><div><h2>新建抓取任务</h2><p>选择平台并粘贴需要验证的真实商品链接。</p></div></div><span className={styles.liveBadge}><span />异步队列</span></div>
            <form onSubmit={createTask} className={styles.form}>
              <div className={styles.fieldGrid}>
                <label>平台<select value={platform} onChange={(event) => setPlatform(event.target.value as Platform)}>{platformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
                <label>最大尝试次数<select value={maxAttempts} onChange={(event) => setMaxAttempts(Number(event.target.value))}><option value={1}>1 次</option><option value={2}>2 次</option><option value={3}>3 次</option></select></label>
              </div>
              <label>商品 URL<div className={styles.urlInput}><input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="例如：淘宝商品链接或短链接" inputMode="url" /><span>{urlHint ? `${urlHint.label} · 已识别` : "支持 1688、淘宝 / 天猫、京东、拼多多"}</span></div></label>
              <p className={styles.accountHint}><ShieldCheck size={14} />账号由管理员统一维护，提交任务时系统会自动选择当前可用的账号池。</p>
              <label className={styles.checkbox}><input type="checkbox" checked={includeDescImages} onChange={(event) => setIncludeDescImages(event.target.checked)} /><span>同时抓取详情图和 SKU 图（会增加任务耗时）</span></label>
              <button className={styles.primaryButton} type="submit" disabled={loading || !workspace?.id}>{loading ? <LoaderCircle size={17} className="spin" /> : <Plus size={17} />}{loading ? "正在提交…" : "提交真实抓取任务"}</button>
            </form>
            {message ? <p className={styles.successMessage}><CheckCircle2 size={15} />{message}</p> : null}
            {error ? <p className={styles.errorMessage} role="alert"><AlertCircle size={15} />{error}</p> : null}
          </section>

          <section className={styles.card}>
            <div className={styles.cardHeading}><div><span className={styles.step}>02</span><div><h2>任务历史</h2><p>显示当前工作区最近的真实爬虫任务，刷新页面后仍可继续查看状态和结果。</p></div></div><Clock3 size={19} className={styles.headingIcon} /></div>
            {tasks.length ? <div className={styles.taskList}>{tasks.map((item) => <button type="button" key={item.id} className={`${styles.taskRow} ${selectedTaskId === item.id ? styles.taskRowActive : ""}`} onClick={() => setSelectedTaskId(item.id)}><span className={`${styles.statusDot} ${styles[`tone-${statusTone[item.status]}`]}`} /><span className={styles.taskRowMain}><strong>{item.source_url}</strong><small>{formatPlatform(item.platform)} · {formatTime(item.created_at)}</small></span><span className={`${styles.statusPill} ${styles[`tone-${statusTone[item.status]}`]}`}>{statusLabels[item.status]}</span><ChevronDown size={16} className={styles.rowChevron} /></button>)}</div> : <div className={styles.empty}><Clock3 size={24} /><strong>还没有抓取任务</strong><span>提交第一个真实商品链接后，任务会出现在这里。</span></div>}
          </section>
        </main>

        <aside className={styles.detailColumn}>
          <section className={`${styles.card} ${styles.detailCard}`}>
            <div className={styles.cardHeading}><div><span className={styles.step}>03</span><div><h2>任务详情</h2><p>{task ? `任务 ${task.id}` : "选择一个任务查看实时状态"}</p></div></div>{detailLoading ? <LoaderCircle size={18} className="spin" /> : null}</div>
            {task ? <>
              <div className={styles.statusHeader}><div><span className={`${styles.statusPill} ${styles[`tone-${statusTone[task.status]}`]}`}>{statusLabels[task.status]}</span><strong>{task.stage || "—"}</strong></div><span>{task.progress}%</span></div>
              <div className={styles.progress}><span style={{ width: `${task.progress}%` }} /></div>
              <dl className={styles.metaGrid}><div><dt>平台</dt><dd>{formatPlatform(task.platform)}</dd></div><div><dt>尝试</dt><dd>{task.attempt_count} / {task.max_attempts}</dd></div><div><dt>执行方式</dt><dd>系统自动分配</dd></div><div><dt>开始时间</dt><dd>{formatTime(task.started_at)}</dd></div></dl>
              {task.error_message_safe ? <div className={styles.taskError}><AlertCircle size={16} /><div><strong>{task.error_code || "任务异常"}</strong><span>{task.error_message_safe}</span></div></div> : null}
              <div className={styles.actionRow}>{canCancel ? <button className={styles.outlineButton} type="button" onClick={() => void cancelTask()} disabled={loading || task.status === "cancelling"}><Square size={14} />取消任务</button> : null}{canRetry ? <button className={styles.outlineButton} type="button" onClick={() => void retryTask()} disabled={loading}><RotateCcw size={14} />重新排队</button> : null}<button className={styles.iconButton} type="button" onClick={() => void loadTaskDetail(task.id)} disabled={detailLoading} aria-label="刷新任务"><RefreshCw size={15} className={detailLoading ? "spin" : ""} /></button></div>
              <div className={styles.subsection}><h3>事件时间线</h3>{events.length ? <ol className={styles.timeline}>{events.slice().reverse().map((event) => <li key={event.id}><span className={styles.timelineIcon}>{eventIcon(event)}</span><div><strong>{event.message}</strong><small>{formatTime(event.created_at)}{event.to_status ? ` · ${statusLabels[event.to_status as CrawlStatus] ?? event.to_status}` : ""}</small></div></li>)}</ol> : <p className={styles.mutedText}>暂无事件。</p>}</div>
            </> : <div className={styles.empty}><CircleDot size={24} /><strong>等待选择任务</strong><span>提交任务后，这里会显示队列、会话和结果状态。</span></div>}
          </section>
        </aside>
      </div>

      {task?.status === "succeeded" && result ? (
        <section className={`${styles.card} ${styles.resultCardSection}`}>
          <div className={styles.cardHeading}>
            <div><span className={styles.step}>04</span><div><h2>商品结果与内部资产</h2><p>按主图、详情图和 SKU 图分别查看抓取结果。</p></div></div>
            <span className={styles.resultCount}>{new Set(assets.map(({ id }) => id)).size} 张不同图片</span>
          </div>
          <div className={styles.productSummary}>
            <div><span className={styles.resultPlatform}>{formatPlatform(result.platform)}</span><h2>{result.title || "未返回商品标题"}</h2><p>{result.shopName || "未返回店铺"} · {result.price || "价格未返回"}{result.sales ? ` · ${result.sales}` : ""}</p></div>
            {result.numIid ? <code>{result.numIid}</code> : null}
          </div>
          {result.warnings?.length ? <div className={styles.warningList}>{result.warnings.map((warning) => <span key={warning}><AlertCircle size={14} />{warning}</span>)}</div> : null}
          {result.skuPropGroups?.map((group, index) => {
            const mappedImages = Object.values(group.image_urls ?? {}).filter(Boolean);
            const shared = mappedImages.length > new Set(mappedImages).size;
            return (
              <div className={styles.skuGroup} key={`${group.name}-${index}`}>
                <h3>{group.name} · {group.values.length} 个选项</h3>
                <div className={styles.skuOptions}>{group.values.map((value) => <span key={value}>{value}</span>)}</div>
                {shared ? <p>京东页面的部分规格共用同一张图片，SKU 图片已去重展示。</p> : null}
              </div>
            );
          })}
          {([['main', '主图'], ['detail', '详情图'], ['sku', 'SKU 图']] as const).map(([role, label]) => {
            const ids = [...new Set(assetGroups[role] ?? [])];
            return (
              <div className={styles.imageGroup} key={role}>
                <h3>{label} <span>{ids.length} 张</span></h3>
                {ids.length ? <div className={styles.assetGrid}>{ids.map((id) => <AssetThumb key={id} workspaceId={task.workspace_id} assetId={id} label={`${label} · ${id}`} />)}</div> : <p className={styles.mutedText}>本次未取得{label}。</p>}
              </div>
            );
          })}
          <Link className={styles.resultLink} href="/assets">前往个人素材库查看全部资产 <ExternalLink size={14} /></Link>
        </section>
      ) : null}
    </div>
  );
}
