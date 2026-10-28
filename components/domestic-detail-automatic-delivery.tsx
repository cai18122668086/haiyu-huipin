"use client";

import { useRecoverAutomaticDelivery, type AutomaticDeliveryAuthorization } from "@/features/jobs/api/automatic-delivery";
import type { JobDetail } from "@/lib/api/types";
import { errorMessage } from "@/lib/api/request";

export function AutomaticDeliveryChoice({ requested, quote, selected, busy, error, onChange }: { requested: boolean; quote?: AutomaticDeliveryAuthorization; selected?: AutomaticDeliveryAuthorization; busy: boolean; error?: unknown; onChange: (requested: boolean, authorization?: AutomaticDeliveryAuthorization) => void }) {
  const matches = quote && selected?.sha256 === quote.sha256;
  return <section className="detail-form-card" aria-label="交付方式授权">
    <h2>交付方式</h2>
    <p>默认在生成完成后等待你审阅结果并确认交付。</p>
    <label><input type="checkbox" style={{ width: "auto", marginRight: 8 }} checked={requested} onChange={event => onChange(event.target.checked)} />在方案确认时另行授权自动交付</label>
    {requested && <div>
      {busy && <p>正在核对当前方案的自动交付授权范围…</p>}
      {error !== undefined && <p role="alert">{errorMessage(error)}</p>}
      {quote && <><p>本次授权绑定方案版本 {quote.plan_revision}。</p><p>{quote.completion_disclosure}</p>
        <label><input type="checkbox" style={{ width: "auto", marginRight: 8 }} checked={Boolean(matches)} disabled={busy || error !== undefined} onChange={event => onChange(true, event.target.checked ? quote : undefined)} />{quote.authorization_text}</label>
        <p><a download="automatic-delivery-authorization.json" href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(quote, null, 2))}`}>下载本次授权说明</a></p></>}
    </div>}
  </section>;
}

export function AutomaticDeliveryRecovery({ workspaceId, job, authorization }: { workspaceId?: string; job: JobDetail; authorization: AutomaticDeliveryAuthorization }) {
  const recover = useRecoverAutomaticDelivery(workspaceId);
  const blocked = job.stage === "automatic_delivery_reconciliation_required";
  return <div>
    <p>{blocked ? "自动打包尚待恢复。生成图片与原费用保留，恢复只核对并完成原打包，不调用模型或重新生图。" : "正在按方案阶段的明确授权核对并完成自动交付。"}</p>
    {blocked && <button className="primary-button" type="button" disabled={recover.isPending} onClick={() => recover.mutate({ jobId: job.id, authorizationSha256: authorization.sha256 })}>{recover.isPending ? "正在提交恢复…" : "恢复已授权的原自动打包"}</button>}
    {recover.error && <p role="alert">{errorMessage(recover.error)}</p>}
  </div>;
}
