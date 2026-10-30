"use client";
import { JobCreditSettlement } from "@/components/job-credit-settlement";

import {
  Ban,
  Check,
  FileWarning,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  X,
} from "lucide-react";
import Link from "next/link";

import { JobAssetPreview } from "@/components/job-asset-preview";
import { JobDeliverablesDownload } from "@/components/job-deliverables-download";
import { JobResultImage } from "@/components/job-result-image";
import { StatusBadge } from "@/components/status-badge";
import { useJob } from "@/features/jobs/api/jobs";
import { errorMessage } from "@/lib/api/request";
import type { JobDetail } from "@/lib/api/types";
import { allowsJobAction } from "@/lib/jobs/actions";
import {
  formatDateTime,
  jobAssetRoleLabel,
  jobStageLabel,
  jobTitle,
  jobTypeLabel,
  safeJobFailureSummary,
} from "@/lib/jobs/presentation";
import {
  canDownloadJobArchive,
  selectJobInputAssets,
  selectJobIntermediateAssets,
  selectJobReferenceAssets,
  selectJobResultAssets,
} from "@/lib/jobs/results";
import { jobWorkspaceHref } from "@/lib/jobs/workspace-link";
import { JobItemList } from "./job-item-list";
import { JobProgress } from "./job-progress";

function resultEmptyMessage(job: JobDetail): string {
  if (job.status === "completed") {
    return "任务已经完成，但后端没有登记可交付输出资产。输入素材不会被当作生成结果。";
  }
  if (job.status === "failed") {
    return "任务生成失败，没有可交付输出。输入素材不会被当作生成结果。";
  }
  if (job.status === "partial") {
    return "当前没有可交付输出，请查看执行明细并重试失败项目。";
  }
  if (job.status === "cancelled") {
    return "任务已取消，没有生成可交付输出。";
  }
  return "输出结果将在生成完成后显示，这里不会使用输入素材占位。";
}

export function JobDetailPanel({
  workspaceId,
  jobId,
  onClose,
  onCancel,
  onRetry,
  onRetryItem,
  onConfirmPlan,
  cancelPending,
  retryPending,
  confirmPending,
}: {
  workspaceId: string;
  jobId: string;
  onClose: () => void;
  onCancel: (job: JobDetail) => void;
  onRetry: (job: JobDetail) => void;
  onRetryItem: (job: JobDetail, itemId: string) => void;
  onConfirmPlan: (job: JobDetail) => void;
  cancelPending: boolean;
  retryPending: boolean;
  confirmPending: boolean;
}) {
  const detail = useJob(workspaceId, jobId);
  const job = detail.data;
  const inputAssets = selectJobInputAssets(job);
  const referenceAssets = selectJobReferenceAssets(job);
  const resultAssets = selectJobResultAssets(job);
  const intermediateAssets = selectJobIntermediateAssets(job);
  const permittedActions = job?.allowed_actions ?? [];

  return (
    <>
      <button className="drawer-overlay" onClick={onClose} aria-label="关闭任务详情" />
      <aside className="task-drawer open job-detail-drawer" role="dialog" aria-modal="true" aria-label="任务详情">
        <div className="drawer-head">
          <div><p>生成记录</p><strong>{job ? jobTitle(job) : "正在加载任务"}</strong></div>
          <button className="icon-button" onClick={onClose} aria-label="关闭任务详情"><X size={19} /></button>
        </div>
        {detail.isPending && !job && <div className="api-state"><LoaderCircle className="spin" /><p>正在加载任务详情...</p></div>}
        {detail.error && !job && (
          <div className="api-state api-state--error" role="alert">
            <FileWarning /><p>{errorMessage(detail.error)}</p>
            <button className="secondary-button" onClick={() => void detail.refetch()}>重试</button>
          </div>
        )}
        {job && (
          <div className="job-detail-content">
            <div className="job-detail-heading">
              <div><StatusBadge status={job.status} /><span>{jobTypeLabel(job.type)}</span></div>
              <button type="button" className="icon-button" onClick={() => void detail.refetch()} disabled={detail.isFetching} aria-label="刷新任务详情" title="刷新">
                <RefreshCw size={16} className={detail.isFetching ? "spin" : ""} />
              </button>
            </div>
            <JobProgress job={job} error={detail.error} lastSyncedAt={detail.dataUpdatedAt} refreshing={detail.isFetching} onRefresh={() => void detail.refetch()} />
            <Link className="secondary-button" href={jobWorkspaceHref(job)}>打开对应工作台</Link><JobCreditSettlement workspaceId={workspaceId} jobId={job.id} />

            <section className="job-detail-section">
              <div className="job-detail-section__head"><div><span className="job-section-eyebrow">任务信息</span><h3>任务概况</h3></div><small>{detail.isFetching ? "更新中…" : "实时更新"}</small></div>
              <dl className="job-detail-facts">
                <div><dt>任务编号</dt><dd>{job.id}</dd></div>
                <div><dt>任务类型</dt><dd>{jobTypeLabel(job.type)}</dd></div>
                <div><dt>阶段</dt><dd>{jobStageLabel(job.stage, job.status)}</dd></div>
                <div><dt>创建时间</dt><dd>{formatDateTime(job.created_at)}</dd></div>
                <div><dt>开始时间</dt><dd>{formatDateTime(job.started_at)}</dd></div>
                <div><dt>更新时间</dt><dd>{formatDateTime(job.updated_at)}</dd></div>
                <div><dt>完成时间</dt><dd>{formatDateTime(job.finished_at)}</dd></div>
                <div><dt>记录保留至</dt><dd>{formatDateTime(job.history_expires_at)}</dd></div>
                <div><dt>成功 / 失败 / 总数</dt><dd>{job.succeeded_item_count} / {job.failed_item_count} / {job.expected_item_count}</dd></div>
              </dl>
            </section>

            <section className="job-detail-section" aria-labelledby="job-input-assets-heading">
              <div className="job-detail-section__head"><div><span className="job-section-eyebrow">输入素材</span><h3 id="job-input-assets-heading">本次提交的素材</h3></div><small>{inputAssets.length} 项</small></div>
              {inputAssets.length > 0 ? <div className="job-asset-grid">{inputAssets.map((asset) => <JobAssetPreview key={`${asset.asset_id}-${asset.role}-${asset.ordinal}`} workspaceId={workspaceId} relation={asset} />)}</div> : <div className="job-empty-panel">本任务没有登记输入素材。</div>}
            </section>

            {referenceAssets.length > 0 && (
              <section className="job-detail-section" aria-labelledby="job-reference-assets-heading">
                <div className="job-detail-section__head"><div><span className="job-section-eyebrow">参考素材</span><h3 id="job-reference-assets-heading">构图、风格或姿势参考</h3></div><small>{referenceAssets.length} 项</small></div>
                <div className="job-asset-grid">{referenceAssets.map((asset) => <JobAssetPreview key={`${asset.asset_id}-${asset.role}-${asset.ordinal}`} workspaceId={workspaceId} relation={asset} />)}</div>
              </section>
            )}

            <section className="job-detail-section job-output-section" aria-labelledby="job-output-results-heading">
              <div className="job-detail-section__head"><div><span className="job-section-eyebrow">输出结果</span><h3 id="job-output-results-heading">可交付图片</h3></div><small>{resultAssets.length} 项</small></div>
              {resultAssets.length > 0 ? (
                <>
                  <div className="job-output-grid job-results-grid">
                    {resultAssets.map((asset, index) => (
                      <JobResultImage
                        key={`${asset.asset_id}-${asset.ordinal}`}
                        workspaceId={workspaceId}
                        assetId={asset.asset_id}
                        alt={`${jobTitle(job)} 输出结果 ${index + 1}`}
                        label={`${index + 1}. ${jobAssetRoleLabel(asset.role)}`}
                      />
                    ))}
                  </div>
                  {canDownloadJobArchive(job) ? <JobDeliverablesDownload workspaceId={workspaceId} jobId={job.id} /> : null}
                </>
              ) : (
                <div className="job-empty-panel job-empty-panel--result">{resultEmptyMessage(job)}</div>
              )}
            </section>

            {intermediateAssets.length > 0 && (
              <section className="job-detail-section" aria-labelledby="job-intermediate-assets-heading">
                <div className="job-detail-section__head"><div><span className="job-section-eyebrow">中间产物</span><h3 id="job-intermediate-assets-heading">处理过程记录</h3></div><small>{intermediateAssets.length} 项</small></div>
                <div className="job-intermediate-list">
                  {intermediateAssets.map((asset) => <div key={`${asset.asset_id}-${asset.ordinal}`}><strong>{jobAssetRoleLabel(asset.role)}</strong><span>仅用于生成校验，不作为最终结果展示或下载。</span></div>)}
                </div>
              </section>
            )}

            {job.error_code && <section className="job-detail-error" role="alert"><FileWarning size={18} /><div><strong>任务未完成</strong><p>{safeJobFailureSummary(job.error_code)}</p><small>错误码：{job.error_code}</small></div></section>}

            <section className="job-detail-section">
              <div className="job-detail-section__head"><div><span className="job-section-eyebrow">执行明细</span><h3>分项状态</h3></div><small>{job.items.length} 项</small></div>
              <JobItemList job={job} onRetryItem={(itemId) => onRetryItem(job, itemId)} retryPending={retryPending} />
            </section>

            <section className="job-detail-actions" aria-label="任务操作">
              <div className="job-detail-section__head"><div><span className="job-section-eyebrow">可用操作</span><h3>继续处理</h3></div></div>
              <div className="job-action-buttons"><Link className="secondary-button" href={`/account?jobId=${encodeURIComponent(job.id)}#help`}>联系帮助，关联此任务</Link>
                {allowsJobAction(permittedActions, "retry") && <button type="button" className="secondary-button" onClick={() => onRetry(job)} disabled={retryPending}><RotateCcw size={15} />重试失败项</button>}
                {allowsJobAction(permittedActions, "cancel") && <button type="button" className="secondary-button danger-button" onClick={() => onCancel(job)} disabled={cancelPending}><Ban size={15} />取消任务</button>}
                {allowsJobAction(permittedActions, "confirm_plan") && job.plan && <button type="button" className="primary-button" onClick={() => onConfirmPlan(job)} disabled={confirmPending}><Check size={15} />确认方案</button>}
                {!permittedActions.length && <p className="job-no-actions"><ShieldAlert size={15} />当前任务没有可用操作</p>}
              </div>
            </section>
          </div>
        )}
      </aside>
    </>
  );
}
