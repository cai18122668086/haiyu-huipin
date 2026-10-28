"use client";

import { CheckCircle2, Clock3, Info, LoaderCircle, RefreshCw } from "lucide-react";
import { useSyncExternalStore } from "react";
import { errorMessage } from "@/lib/api/request";
import { formatBeijingDate } from "@/lib/datetime.mjs";
import { formatJobElapsed, jobElapsedMilliseconds, presentJobProgress, type ProgressJob } from "@/lib/jobs/progress-feedback";
import styles from "./job-progress-feedback.module.css";

let clock = 0;
let clockTimer: ReturnType<typeof setInterval> | undefined;
const listeners = new Set<() => void>();
function subscribeClock(listener: () => void) {
  listeners.add(listener);
  clock = Date.now();
  clockTimer ??= setInterval(() => { clock = Date.now(); listeners.forEach((notify) => notify()); }, 1000);
  return () => {
    listeners.delete(listener);
    if (!listeners.size) { clearInterval(clockTimer); clockTimer = undefined; }
  };
}
const clockSnapshot = () => clock;
const noClock = () => null;
const noSubscription = () => () => {};
function subscribeOnline(notify: () => void) {
  window.addEventListener("online", notify);
  window.addEventListener("offline", notify);
  return () => { window.removeEventListener("online", notify); window.removeEventListener("offline", notify); };
}
const onlineSnapshot = () => navigator.onLine;
const serverOnline = () => true;

export function JobProgressFeedback({ job, pending = false, pendingLabel = "正在准备任务", error, onRefresh, refreshing = false, lastSyncedAt, summary, suppressFailureDetails = false }: {
  job?: ProgressJob;
  pending?: boolean;
  pendingLabel?: string;
  error?: unknown;
  onRefresh?: () => void;
  refreshing?: boolean;
  lastSyncedAt?: number;
  summary?: string;
  suppressFailureDetails?: boolean;
}) {
  const presentation = job && !pending ? presentJobProgress(job) : undefined;
  const now = useSyncExternalStore(job && !presentation?.terminal && !pending ? subscribeClock : noSubscription, clockSnapshot, noClock);
  const online = useSyncExternalStore(subscribeOnline, onlineSnapshot, serverOnline);
  const disconnected = !online || Boolean(error);
  const moving = !disconnected && (pending || presentation?.running);
  const indeterminate = pending || presentation?.indeterminate;
  const elapsed = job && !pending ? jobElapsedMilliseconds(job, now) : null;
  const detail = !suppressFailureDetails && job && "error_message" in job ? job.error_message : null;
  if (!job && !pending) return null;
  const title = pending ? pendingLabel : presentation!.title;
  const description = pending ? "正在保存素材与参数，请稍候。" : presentation!.description;
  return <section className={styles.feedback} aria-label="任务进度反馈" data-state={presentation?.terminal ? "ended" : "active"}>
    <div className={styles.heading}>
      <span className={styles.icon} aria-hidden="true">{moving ? <LoaderCircle className={styles.spin} size={20} /> : job?.status === "completed" ? <CheckCircle2 size={20} /> : <Info size={20} />}</span>
      <div className={styles.copy} role="status"><strong>{title}</strong><p>{description}</p></div>
    </div>
    <div className={styles.meta}>
      <strong>{pending ? "任务准备中" : summary ?? presentation!.countText}</strong>
      {elapsed !== null && <span aria-live="off"><Clock3 size={13} aria-hidden="true" />{presentation?.terminal ? "本次用时" : "已用时"} {formatJobElapsed(elapsed)}</span>}
    </div>
    <div className={styles.track} role="progressbar" aria-label={indeterminate ? `${title}，等待进度更新` : "任务完成进度"}
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={indeterminate ? undefined : presentation?.progress ?? undefined}
      aria-valuetext={pending ? title : `${presentation!.countText}，${title}`}
      data-indeterminate={indeterminate || undefined} data-moving={moving || undefined}>
      <i style={{ width: indeterminate ? undefined : `${presentation?.progress ?? 0}%` }} />
    </div>
    <div className={styles.connection}>
      <span>{disconnected ? !online ? "当前网络已断开，恢复后将继续同步进度。" : "进度暂未同步，已保留上次读取的任务状态。" : pending ? "请勿重复提交" : lastSyncedAt ? `最近同步 ${formatBeijingDate(lastSyncedAt, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : "任务进度会自动更新"}</span>
      {onRefresh && <button type="button" onClick={onRefresh} disabled={!online || refreshing}>
        <RefreshCw size={13} className={refreshing ? styles.spin : undefined} aria-hidden="true" />{disconnected ? "重新连接" : "刷新状态"}
      </button>}
    </div>
    {presentation?.running && elapsed !== null && elapsed >= 120000 && <p className={styles.note}>较长任务需要更多时间，可离开此页，稍后从生成记录继续查看。</p>}
    {Boolean(detail || error) && <details className={styles.details}><summary>{error ? "查看同步问题" : "查看未完成原因"}</summary><p>{error ? errorMessage(error) : detail}</p>{Boolean(error) && detail && <p>{detail}</p>}</details>}
  </section>;
}
