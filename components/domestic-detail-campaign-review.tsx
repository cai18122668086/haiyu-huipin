"use client";

import { useEffect, useRef, useState } from "react";
import { useJob } from "@/features/jobs/api/jobs";
import { CAMPAIGN_AUTHORIZATION, campaignTerminal, getCampaignPreview, useCampaignHistory, useCampaignQuote, useCampaignReport, useCampaignRecoveryQuote, useCreateCampaignReview, useRecoverCampaignReview, type CampaignQuote, type CampaignReport, type CampaignSelection } from "@/features/jobs/api/campaign-reviews";
import type { JobDetail } from "@/lib/api/types";
import { errorMessage } from "@/lib/api/request";
import { StatusBadge } from "@/components/status-badge";

export function CampaignAuthorization({ workspaceId, sourceId, quote, disabled, onOpen }: { workspaceId?: string; sourceId: string; quote: CampaignQuote; disabled: boolean; onOpen: (id: string) => void }) {
  const [authorized, setAuthorized] = useState(false);
  const create = useCreateCampaignReview(workspaceId);
  return <div>
    <p>{quote.model} · 一次模型审核 · 不生成图片 · 费用预留 USD {(quote.reserve_micros / 1_000_000).toFixed(6)}（实际费用以任务账单为准）</p>
    <label><input type="checkbox" style={{ width: "auto", marginRight: 8 }} disabled={disabled || create.isPending} checked={authorized} onChange={event => setAuthorized(event.target.checked)} />{CAMPAIGN_AUTHORIZATION}</label>
    <p><button type="button" disabled={!authorized || disabled || create.isPending} onClick={async () => {
      try { const job = await create.mutateAsync({ sourceId, quote, approvalText: CAMPAIGN_AUTHORIZATION }); setAuthorized(false); onOpen(job.id); } catch { /* The mutation displays the error and retains its HTTP intent key. */ }
    }}>{create.isPending ? "正在提交…" : "授权并创建独立审核任务"}</button></p>
    {create.error && <p role="alert">{errorMessage(create.error)}</p>}
  </div>;
}

function CampaignPreview({ workspaceId, reviewId }: { workspaceId: string; reviewId: string }) {
  const [url, setUrl] = useState<string>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  const current = useRef<string | undefined>(undefined);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; if (current.current) URL.revokeObjectURL(current.current); }; }, []);
  return <div>
    <button type="button" disabled={pending} onClick={async () => {
      setPending(true); setError(undefined);
      try { const data = await getCampaignPreview(workspaceId, reviewId); if (!mounted.current) return;
        if (current.current) URL.revokeObjectURL(current.current);
        current.current = URL.createObjectURL(data); setUrl(current.current);
      } catch (failure) { if (mounted.current) setError(failure); }
      finally { if (mounted.current) setPending(false); }
    }}>{pending ? "正在核对预览…" : "查看模型实际所见预览"}</button>
    {error !== undefined && <p role="alert">{errorMessage(error)}</p>}
    {url && <figure><div style={{ maxHeight: 520, overflow: "auto" }}><a href={url} target="_blank" rel="noreferrer"><img src={url} alt="独立审核模型实际所见的整页预览" style={{ width: "100%", height: "auto" }} /></a></div><figcaption><a href={url} download="campaign-model-preview.png">下载原始预览</a></figcaption></figure>}
  </div>;
}

export function CampaignReportContent({ data, reviewId, selected, canSelect, onSelect }: { data: CampaignReport; reviewId: string; selected?: CampaignSelection; canSelect: boolean; onSelect: (selection: CampaignSelection) => void }) {
  const fingerprint = typeof data.report.sha256 === "string" ? data.report.sha256 : "";
  const accepted = data.report.native_gate_accepted === true;
  const isSelected = selected?.review_job_id === reviewId && selected.report_sha256 === fingerprint;
  const available = data.original_message_state === "PRESERVED_UTF8" && typeof data.original_message === "string";
  return <div>
    <h3>{accepted ? "独立审核记录已通过原生校验" : "独立报告未被原生校验接受"}</h3>
    <p>此状态属于独立审核记录；最终交付仍需人工审阅全部图片、审核范围和残差。</p>
    {!available && <p role="alert">没有可验证的原始模型回复，不视为审核通过。费用结果未知时，应先核对原任务账单。</p>}
    <details><summary>完整审核报告与校验结果</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 360, overflow: "auto" }}>{JSON.stringify(data.report, null, 2)}</pre></details>
    {available && <details><summary>原始模型回复（完整原文）</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 360, overflow: "auto" }}>{data.original_message}</pre><a download="campaign-original-message.txt" href={`data:text/plain;charset=utf-8,${encodeURIComponent(data.original_message!)}`}>下载原始回复</a></details>}
    <p><a download="campaign-review.json" href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(data, null, 2))}`}>下载完整独立审核记录</a></p>
    {canSelect && <button type="button" disabled={isSelected || !/^[0-9a-f]{64}$/.test(fingerprint)} onClick={() => onSelect({ review_job_id: reviewId, report_sha256: fingerprint })}>{isSelected ? "已列入当前最终审阅" : "将此报告列入最终审阅"}</button>}
  </div>;
}

function CampaignReviewDetails({ workspaceId, sourceId, reviewId, selected, canSelect, onSelect, onOpen }: { workspaceId?: string; sourceId: string; reviewId: string; selected?: CampaignSelection; canSelect: boolean; onSelect: (selection: CampaignSelection) => void; onOpen: (id: string) => void }) {
  const job = useJob(workspaceId, reviewId);
  const report = useCampaignReport(workspaceId, sourceId, job.data);
  const [checkRecovery, setCheckRecovery] = useState(false);
  const recoveryQuote = useCampaignRecoveryQuote(workspaceId, reviewId, checkRecovery && campaignTerminal(job.data?.status));
  const recover = useRecoverCampaignReview(workspaceId);
  return <article>
    <h3>当前查看的审核任务：{reviewId}</h3>
    {job.data && <><StatusBadge status={job.data.status} /><p>实际费用：{job.data.cost.actual?.micros == null ? "尚未确定" : `USD ${(Number(job.data.cost.actual.micros) / 1_000_000).toFixed(6)}`} · 用量完整性：{job.data.cost.usage_completeness}</p></>}
    {job.error && <p role="alert">{errorMessage(job.error)}</p>}
    {report.isFetching && <p>正在核对原始审核证据…</p>}
    {report.error && <p role="alert">报告尚不可读取：{errorMessage(report.error)}</p>}
    {report.data && <><CampaignReportContent data={report.data} reviewId={reviewId} selected={selected} canSelect={canSelect} onSelect={onSelect} />{workspaceId && <CampaignPreview key={reviewId} workspaceId={workspaceId} reviewId={reviewId} />}</>}
    {campaignTerminal(job.data?.status) && <div>
      <p>本地中断可尝试使用已保存的完整原回复恢复。恢复保持旧观察和费用状态，不会重新请求模型。</p>
      <button type="button" disabled={recoveryQuote.isFetching} onClick={() => { setCheckRecovery(true); if (checkRecovery) void recoveryQuote.refetch(); }}>检查原结果恢复</button>
      {recoveryQuote.error && <p>当前没有可验证的完整原始结果可恢复：{errorMessage(recoveryQuote.error)}</p>}
      {recoveryQuote.data && <p><button type="button" disabled={recover.isPending} onClick={async () => { try { const value = await recover.mutateAsync({ reviewId, quote: recoveryQuote.data! }); onOpen(value.id); } catch { /* Preserve the HTTP key for a transport-unknown result. */ } }}>{recover.isPending ? "正在提交恢复…" : "用原回复创建恢复任务（模型 0 次，新增费用 0）"}</button></p>}
      {recover.error && <p role="alert">{errorMessage(recover.error)}</p>}
    </div>}
  </article>;
}

export function DomesticDetailCampaignReview({ workspaceId, source, fingerprint, selected, onSelection, canAttach = true }: { workspaceId?: string; source: JobDetail; fingerprint?: string; selected?: CampaignSelection; onSelection: (selection: CampaignSelection | undefined) => void; canAttach?: boolean }) {
  const history = useCampaignHistory(workspaceId, source.id);
  const quote = useCampaignQuote(workspaceId, source.id, fingerprint);
  const [viewId, setViewId] = useState<string>();
  const items = history.data?.pages.flatMap(page => page.items) ?? [];
  const active = items.some(item => ["queued", "processing", "cancelling"].includes(item.status));
  const canSelect = source.status === "awaiting_final_review" && canAttach;
  return <section className="detail-form-card" aria-label="独立整页审核">
    <h2>可选整页审核</h2>
    <p>{canSelect ? "独立核对整页节奏、跨组接缝与信息重复。审核任务完成后，由你明确选择是否把报告列入最终审阅。" : "独立核对整页节奏、跨组接缝与信息重复，报告保留为后续独立记录。"}</p>
    {source.status === "completed" && <p>{canAttach ? "此详情已确认交付" : "此详情已按原授权完成交付"}；后续审核保留为独立记录，已接受的交付保持原样。</p>}
    {source.status !== "completed" && !canAttach && <p>此任务已有方案阶段的自动交付授权；独立审核作为后续记录，不改变原交付范围。</p>}
    {quote.error && <p role="alert">报价不可用：{errorMessage(quote.error)}</p>}
    {quote.data && <CampaignAuthorization key={`${workspaceId}:${source.id}:${quote.data.sha256}`} workspaceId={workspaceId} sourceId={source.id} quote={quote.data} disabled={active || history.isPending || history.isError || quote.isError || quote.isFetching || !fingerprint} onOpen={setViewId} />}
    {active && <p>当前来源已有审核任务在进行，请等待其结束。</p>}
    <h3>本详情的审核历史</h3>
    <button type="button" onClick={() => { void history.refetch(); if (fingerprint) void quote.refetch(); }}>刷新审核记录与报价</button>
    {history.error && <p role="alert">{errorMessage(history.error)}</p>}
    {!history.isPending && !items.length && <p>尚未创建独立整页审核。</p>}
    <ul>{items.map(item => <li key={item.id}><button type="button" aria-pressed={viewId === item.id} onClick={() => setViewId(item.id)}>{item.display_name ?? item.id} · {item.id}</button> <StatusBadge status={item.status} /></li>)}</ul>
    {history.hasNextPage && <button type="button" disabled={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>加载更早的审核</button>}
    {selected && canSelect && <p>最终审阅已选择：{selected.review_job_id} <button type="button" onClick={() => onSelection(undefined)}>不附加独立报告</button></p>}
    {viewId && <CampaignReviewDetails key={`${workspaceId}:${source.id}:${viewId}`} workspaceId={workspaceId} sourceId={source.id} reviewId={viewId} selected={selected} canSelect={canSelect} onSelect={onSelection} onOpen={setViewId} />}
  </section>;
}
