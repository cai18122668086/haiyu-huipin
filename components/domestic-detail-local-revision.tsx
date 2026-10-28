"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { jobKeys } from "@/features/jobs/api/jobs-keys";
import { apiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/generated/openapi";
import { IdempotencyIntentRegistry, intentSignature } from "@/lib/api/idempotency";
import { errorMessage, unwrapApiResponse } from "@/lib/api/request";
import type { DomesticDetailFinalReview } from "@/lib/api/types";

type Delta = components["schemas"]["DomesticDetailLocalRevisionDelta"];
type QuoteRequest = components["schemas"]["DomesticDetailLocalRevisionQuoteRequest"];
type CreateRequest = components["schemas"]["DomesticDetailLocalRevisionCreateRequest"];
export const LOCAL_REVISION_APPROVAL = "我已审阅原方案、原图、本次局部修改范围和最高费用，明确授权创建独立双屏修订任务，其余屏幕保留，生成后等待我审阅确认。";
const lines = (text: string) => text.split(/\r?\n/).filter(line => line.trim());

export function DomesticDetailLocalRevision({ workspaceId, review }: { workspaceId: string; review: DomesticDetailFinalReview }) {
  const router = useRouter();
  const cache = useQueryClient();
  const registry = useRef(new IdempotencyIntentRegistry());
  const [moduleId, setModuleId] = useState("");
  const [kind, setKind] = useState<Delta["change_kind"]>("TYPOGRAPHY_LAYOUT");
  const [instruction, setInstruction] = useState("");
  const [changes, setChanges] = useState("");
  const [preserve, setPreserve] = useState("");
  const [regressions, setRegressions] = useState("");
  const [localConfirmed, setLocalConfirmed] = useState(false);
  const [approvedQuote, setApprovedQuote] = useState("");
  const body: QuoteRequest = {
    final_review_sha256: review.sha256,
    adjustment: {
      module_id: moduleId, change_kind: kind, verbatim_instruction: instruction,
      change_scope: "local", change_only: lines(changes), preserve: lines(preserve), must_not_regress: lines(regressions),
      changes_identity: false, changes_facts: false, changes_target_buyer: false, changes_campaign_style: false, changes_module_sequence: false,
    },
  };
  const signature = intentSignature("local-revision", { jobId: review.job_id, ...body });
  const quote = useMutation({
    mutationFn: async (input: { body: QuoteRequest; signature: string }) => {
      const result = await apiClient.POST("/api/v1/workspaces/{workspace_id}/jobs/{job_id}/local-revision/quote", {
        params: { path: { workspace_id: workspaceId, job_id: review.job_id } }, body: input.body,
      });
      return { value: unwrapApiResponse(result), signature: input.signature };
    },
    retry: false,
  });
  const current = localConfirmed && quote.data?.signature === signature ? quote.data.value : undefined;
  const submit = useMutation({
    mutationFn: async (input: CreateRequest) => {
      const intent = intentSignature("local-revision", { jobId: review.job_id, ...input });
      const result = await apiClient.POST("/api/v1/workspaces/{workspace_id}/jobs/{job_id}/local-revision", {
        params: { path: { workspace_id: workspaceId, job_id: review.job_id }, header: { "Idempotency-Key": registry.current.keyFor(intent) } }, body: input,
      });
      const child = unwrapApiResponse(result);
      registry.current.complete(intent);
      return child;
    },
    retry: false,
    onSuccess: async child => {
      await cache.invalidateQueries({ queryKey: jobKeys.all(workspaceId) });
      router.push(`/workspace/detail-pack?jobId=${encodeURIComponent(child.id)}`);
    },
  });
  const valid = localConfirmed && /^M(?:0[1-9]|1[0-2])$/.test(moduleId) && instruction.trim().length >= 2 && [changes, preserve, regressions].every(value => lines(value).length > 0);
  const busy = quote.isPending || submit.isPending;
  return <section className="detail-form-card" aria-label="详情局部修订">
    <div className="form-section-head"><div><h2>修改某一屏</h2><p>创建独立修订任务。所选屏幕与相邻屏一起生成，其余屏幕沿用当前成图；完成后由你审阅。</p></div></div>
    <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
      <label>修改屏幕<select value={moduleId} onChange={event => setModuleId(event.target.value)}><option value="">请选择</option>{review.screens.map(screen => typeof screen.module_id === "string" && <option key={screen.module_id} value={screen.module_id}>{screen.module_id}</option>)}</select></label>
      <label>修改类型<select value={kind} onChange={event => setKind(event.target.value as Delta["change_kind"])}><option value="TYPOGRAPHY_LAYOUT">文字排版</option><option value="COPY_CONTENT">文案内容</option><option value="VISUAL_DETAIL">画面细节</option><option value="OTHER_LOCAL">其他局部调整</option></select></label>
      <label>完整修改说明<textarea value={instruction} maxLength={2000} onChange={event => setInstruction(event.target.value)} placeholder="说明希望看到的具体变化" /></label>
      <label>只修改这些内容（每行一项）<textarea value={changes} onChange={event => setChanges(event.target.value)} /></label>
      <label>必须保留的内容（每行一项）<textarea value={preserve} onChange={event => setPreserve(event.target.value)} /></label>
      <label>不能变差的内容（每行一项）<textarea value={regressions} onChange={event => setRegressions(event.target.value)} /></label>
      <label><input type="checkbox" style={{ width: "auto", marginRight: 8 }} checked={localConfirmed} onChange={event => setLocalConfirmed(event.target.checked)} />本次不改变商品身份、事实、目标买家、全页风格或屏幕顺序。</label>
      <button type="button" className="secondary-button" disabled={!valid} onClick={() => quote.mutate({ body, signature })}>{quote.isPending ? "正在核对范围与费用…" : "核对修改范围与最高费用"}</button>
    </fieldset>
    {quote.error && <p role="alert">{errorMessage(quote.error)}</p>}
    {current && <div>
      <p>将修订 {current.module_ids.join("、")}。最多生成 {current.maximum_image_requests} 次（含一次自动修复），本次最高费用 ${(current.maximum_cost_micros / 1_000_000).toFixed(4)}；结果仍需审阅。</p>
      <label><input type="checkbox" style={{ width: "auto", marginRight: 8 }} checked={approvedQuote === current.sha256} disabled={busy} onChange={event => setApprovedQuote(event.target.checked ? current.sha256 : "")} />{LOCAL_REVISION_APPROVAL}</label>
      <button type="button" className="primary-button" disabled={busy || approvedQuote !== current.sha256} onClick={() => submit.mutate({ ...body, quote_sha256: current.sha256, approval_text: LOCAL_REVISION_APPROVAL })}>{submit.isPending ? "正在创建修订任务…" : "确认并创建独立修订"}</button>
    </div>}
    {submit.error && <p role="alert">{errorMessage(submit.error)}</p>}
  </section>;
}
