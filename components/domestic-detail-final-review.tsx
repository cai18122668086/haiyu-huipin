"use client";

import { NativeApprovalDocument } from "@/components/domestic-detail-approval";
import { JobResultImage } from "@/components/job-result-image";
import type { DomesticDetailFinalReview as FinalReview } from "@/lib/api/types";

export const NATIVE_DETAIL_FINAL_REVIEW_TEXT = "我已审阅当前全部单屏、双屏母版及审核范围，接受所列残差并确认交付。";

export function isCurrentFinalReview(review: FinalReview | undefined, jobId: string, count: number): review is FinalReview {
  if (!review || review.version !== "domestic-detail-final-review-v1" || review.job_id !== jobId || !/^[0-9a-f]{64}$/.test(review.sha256) || !review.document_markdown.trim()) return false;
  if (![8, 10, 12].includes(count) || review.screens.length !== count || review.pair_canvases.length !== count / 2) return false;
  const screens = review.screens.map(screen => screen.asset_id);
  const masters = review.pair_canvases.map(pair => pair.asset_id);
  return [...screens, ...masters].every(id => typeof id === "string" && Boolean(id)) && new Set(screens).size === count && new Set(masters).size === count / 2;
}

export function DomesticDetailFinalReview({ review, workspaceId, reviewed, onReviewed, completed = false }: {
  review: FinalReview; workspaceId?: string; reviewed: boolean;
  onReviewed: (checked: boolean) => void; completed?: boolean;
}) {
  const automatic = review.delivery_mode === "AUTO_FINALIZE_AUTHORIZED";
  return <section className="detail-form-card" aria-label="国内详情最终审阅">
    <div className="form-section-head"><div><h2>{automatic ? (completed ? "已按授权完成自动交付" : "自动交付记录") : (completed ? "已确认的详情交付" : "最终结果审阅")}</h2><p>{review.quality_state === "WITH_RESIDUALS" ? "存在未解决残差，请逐项审阅后决定是否接受。" : "逐屏与组内审核已记录，请检查所有成图。"}</p></div></div>
    {automatic && <p>本记录来自方案阶段的自动交付授权，不表示你已逐屏审阅最终成图。</p>}
    <NativeApprovalDocument document={review.document_markdown} label="完整最终审阅内容" />
    <h3>已接受的双屏母版</h3>
    <div className="job-results-grid">{review.pair_canvases.map((pair, index) => typeof pair.asset_id === "string" && <JobResultImage key={pair.asset_id} workspaceId={workspaceId} assetId={pair.asset_id} alt={`双屏母版 ${index + 1}`} label={`${typeof pair.pair_id === "string" ? pair.pair_id : index + 1} · 连续双屏母版`} />)}</div>
    <p><a download="detail-final-review.md" href={`data:text/markdown;charset=utf-8,${encodeURIComponent(review.document_markdown)}`}>下载完整审阅说明</a>{" · "}<a download="detail-final-review.json" href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(review, null, 2))}`}>下载交付记录</a></p>
    <p style={{ overflowWrap: "anywhere", fontSize: 12 }}>本次审阅指纹：{review.sha256}</p>
    {!completed && !automatic && <label><input type="checkbox" style={{ width: "auto", marginRight: 8 }} checked={reviewed} onChange={event => onReviewed(event.target.checked)} />{NATIVE_DETAIL_FINAL_REVIEW_TEXT}</label>}
  </section>;
}
