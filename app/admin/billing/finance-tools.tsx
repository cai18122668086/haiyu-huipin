"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { formatBeijingDate } from "@/lib/datetime.mjs";
import { errorText, money, request } from "./billing-api";
import styles from "./page.module.css";

const when = (value: string | null) => value ? formatBeijingDate(value, { dateStyle: "medium", timeStyle: "short" }) : "—";
type Refund = { id: string; purchase_id: string; workspace_id: string; amount_fen: number; status: string; reason: string; created_at: string; completed_at: string | null; last_error_code: string | null; credits_revoked: number; future_credits_cancelled: number };
const statusName = (value: string) => ({ requested: "待提交", processing: "渠道处理中", succeeded: "退款成功", failed: "退款未成功" })[value] ?? value;
type Quote = { quote_token: string; max_refundable_amount_fen: number; credits_to_revoke: number; future_credits_to_cancel: number; frozen_credits: number; membership_will_cancel: boolean };

type Reconciliation = { enabled: boolean; healthy: boolean; wechat_confirmation_mode: string; last_completed_at: string | null; pending_orders: number; review_orders: number; checked_count: number; confirmed_count: number; last_error: string | null };
export function FinanceOverview() {
  const monitor = useQuery({ queryKey: ["support", "reconciliation"], queryFn: () => request<Reconciliation>("/reconciliation"), refetchInterval: 10000 });
  const query = useQuery({ queryKey: ["support", "overview"], queryFn: () => request<{ paid_orders: number; gross_amount_fen: number; refunded_amount_fen: number; net_amount_fen: number; pending_refunds: number; channels: Record<string, string> }>("/overview") });
  if (query.isError) return <p role="alert">{errorText(query.error)}</p>;
  if (!query.data) return null;
  const data = query.data;
  return <><div className={styles.stats}><div>收款总额 · {data.paid_orders} 笔<strong>{money(data.gross_amount_fen)}</strong></div><div>已成功退款<strong>{money(data.refunded_amount_fen)}</strong></div><div>净收款<strong>{money(data.net_amount_fen)}</strong></div></div><p>处理中退款 {data.pending_refunds} 笔 · 支付宝：{data.channels.alipay === "ready" ? "已配置" : "未开放"} · 微信：{data.channels.wechat === "ready" ? "已配置" : "未开放"}</p>{monitor.data && <section className={styles.operation} aria-label="后台自动补单状态"><h3>后台自动补单 · {monitor.data.enabled ? monitor.data.healthy ? "运行正常" : "需检查运行状态" : "未开启"}</h3><p>微信确认方式：{monitor.data.wechat_confirmation_mode === "query" ? "服务端主动查单" : "支付回调及查单"} · 待核对 {monitor.data.pending_orders} 笔 · 异常 {monitor.data.review_orders} 笔</p><p>最近完成：{when(monitor.data.last_completed_at)} · 本轮检查 {monitor.data.checked_count} 笔，确认到账 {monitor.data.confirmed_count} 笔。用户关闭页面后仍持续核对。</p>{monitor.data.last_error && <p role="alert">{monitor.data.last_error}</p>}</section>}{monitor.isError && <p role="alert">补单状态暂时无法读取：{errorText(monitor.error)}</p>}</>;
}

export function RefundDialog({ order, close, refresh }: { order: { id: string; package_name: string; amount_fen: number; refunded_amount_fen: number }; close: () => void; refresh: () => void }) {
  const [policy, setPolicy] = useState("unused");
  const [reason, setReason] = useState("");
  const [amount, setAmount] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const intent = useRef<{ fingerprint: string; key: string } | null>(null);
  const quote = useQuery({ queryKey: ["support", "refund-quote", order.id, policy], queryFn: () => request<Quote>(`/orders/${order.id}/refund-quote?policy=${policy}`, { method: "POST" }), retry: false });
  const mutation = useMutation({ mutationFn: () => {
    const body = JSON.stringify({ quote_token: quote.data!.quote_token, policy, reason, ...(amount ? { amount_fen: Math.round(Number(amount) * 100) } : {}) });
    if (intent.current?.fingerprint !== body) intent.current = { fingerprint: body, key: crypto.randomUUID() };
    return request<Refund>(`/orders/${order.id}/refunds`, { method: "POST", body, headers: { "Idempotency-Key": intent.current.key } });
  }, onSuccess: refresh });
  const blocked = !quote.data || quote.data.max_refundable_amount_fen <= 0 || quote.data.frozen_credits > 0;
  return <div className={styles.overlay}><section className={styles.dialog} role="dialog" aria-modal="true" aria-label="原路退款核对"><header className={styles.heading}><h2>原路退款核对</h2><button disabled={mutation.isPending} onClick={close}>关闭</button></header><p>{order.package_name}<small>{order.id}</small></p><p>原收款 {money(order.amount_fen)} · 已退 {money(order.refunded_amount_fen)}</p>
    {mutation.isSuccess ? <div role="status"><h3>{statusName(mutation.data.status)}</h3><p>退款单 {mutation.data.id} · {money(mutation.data.amount_fen)}</p><p>{mutation.data.status === "failed" ? "未成功退款，仍有效的积分与会员权益已恢复。" : mutation.data.status === "succeeded" ? "渠道已确认退款成功，权益已按核对结果回收。" : "退款意图已保存，系统将持续向原渠道核对，请在退款记录中查看进度。"}</p></div> : <form className={styles.operation} onSubmit={e => { e.preventDefault(); mutation.mutate(); }}>
      <label>退款政策<select value={policy} disabled={mutation.isPending} onChange={e => { setPolicy(e.target.value); setConfirmed(false); setAmount(""); }}><option value="unused">按本订单未使用权益比例退款</option><option value="goodwill">客服特批 · 可包含已使用部分</option></select></label>
      {quote.isPending ? <p>正在核对订单权益…</p> : quote.isError ? <p className={styles.error} role="alert">{errorText(quote.error)} <button type="button" onClick={() => void quote.refetch()}>重新核对</button></p> : quote.data && <><p>最多可退 <strong>{money(quote.data.max_refundable_amount_fen)}</strong>；回收本订单剩余 {quote.data.credits_to_revoke} 积分，取消未来 {quote.data.future_credits_to_cancel} 积分发放。{quote.data.membership_will_cancel && "本笔会员将停止。"}</p><p>本操作会回收本订单全部剩余权益。填写较低退款金额也不会保留部分权益；其他订单与人工补发积分保持原状。</p>{quote.data.frozen_credits > 0 && <p role="alert">有 {quote.data.frozen_credits} 积分正在生成中，请等待任务结算。</p>}<label>退款金额（元，留空按最高额度）<input inputMode="decimal" type="number" min="0.01" max={quote.data.max_refundable_amount_fen / 100} step="0.01" value={amount} onChange={e => { setAmount(e.target.value); setConfirmed(false); }} /></label></>}
      <label>退款原因<textarea required minLength={5} maxLength={160} value={reason} onChange={e => { setReason(e.target.value); setConfirmed(false); }} /></label><label><span><input type="checkbox" required checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> 已核对退款金额和权益，确认退回原支付账户</span></label><button disabled={blocked || !confirmed || reason.trim().length < 5 || mutation.isPending}>{mutation.isPending ? "正在提交…" : "确认原路退款"}</button><button type="button" disabled={mutation.isPending} onClick={() => { setConfirmed(false); void quote.refetch(); }}>重新核对额度</button>{mutation.isError && <p role="alert" className={styles.error}>{errorText(mutation.error)}；如果额度已变化，请重新核对。</p>}
    </form>}
  </section></div>;
}

export function RefundRecords({ canAdjust, refresh }: { canAdjust: boolean; refresh: () => void }) {
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const query = useQuery({ queryKey: ["support", "refunds", offset, status, search], queryFn: () => request<{ items: Refund[]; total: number }>(`/refunds?offset=${offset}&status=${status}${search ? `&purchase_id=${encodeURIComponent(search)}` : ""}`.replace("&status=&", "&").replace(/&status=$/, "")), refetchInterval: 30000 });
  const mutation = useMutation({ mutationFn: (id: string) => request<Refund>(`/refunds/${id}/reconcile`, { method: "POST" }), onSuccess: refresh });
  return <><div className={styles.toolbar}><input placeholder="按支付订单号筛选" aria-label="退款关联订单号" value={search} onChange={e => { setSearch(e.target.value.trim()); setOffset(0); }} /><select aria-label="退款状态" value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}><option value="">全部退款状态</option>{["requested", "processing", "succeeded", "failed"].map(value => <option value={value} key={value}>{statusName(value)}</option>)}</select><span>共 {query.data?.total ?? "—"} 笔</span></div>{query.isError && <p role="alert">{errorText(query.error)}</p>}<div className={styles.tableWrap}><table><thead><tr><th>退款单 / 订单</th><th>金额 / 状态</th><th>权益处理</th><th>原因 / 时间</th><th>操作</th></tr></thead><tbody>{query.data?.items.map(row => <tr key={row.id}><td>{row.id}<small>{row.purchase_id}</small><small>{row.workspace_id}</small></td><td>{money(row.amount_fen)}<small>{statusName(row.status)}</small></td><td>回收 {row.credits_revoked}<small>停止后续 {row.future_credits_cancelled}</small></td><td>{row.reason}<small>{when(row.created_at)}</small><small>{row.last_error_code}</small></td><td>{["requested", "processing"].includes(row.status) && <button disabled={!canAdjust || mutation.isPending} onClick={() => mutation.mutate(row.id)}>核对原退款状态</button>}</td></tr>)}</tbody></table>{query.data?.total === 0 && <p className={styles.empty}>暂无退款记录</p>}</div>{mutation.isError && <p className={styles.error} role="alert">{errorText(mutation.error)}</p>}<div className={styles.toolbar}><button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button><button disabled={offset + 30 >= (query.data?.total ?? 0)} onClick={() => setOffset(offset + 30)}>下一页</button></div></>;
}

type Member = { id: string; plan_name: string; monthly_credits: number; months: number; starts_at: string; ends_at: string; purchase_id: string | null; cancelled_at: string | null };
export function MembershipTools({ workspace, canAdjust, refresh }: { workspace: string; canAdjust: boolean; refresh: () => void }) {
  const [product, setProduct] = useState("light_monthly");
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState<string | null>(null);
  const intent = useRef<{ fingerprint: string; key: string } | null>(null);
  const query = useQuery({ queryKey: ["support", "memberships", workspace], queryFn: () => request<{ items: Member[]; products: { id: string; name: string; credits: number }[] }>(`/workspaces/${workspace}/memberships`) });
  const mutation = useMutation({ mutationFn: (target: string) => {
    const body = JSON.stringify(target === "grant" ? { product_id: product, reason } : { reason });
    const fingerprint = workspace + target + body;
    if (intent.current?.fingerprint !== fingerprint) intent.current = { fingerprint, key: crypto.randomUUID() };
    return request(`/workspaces/${workspace}/memberships${target === "grant" ? "" : `/${target}/cancel`}`, { method: "POST", body, headers: { "Idempotency-Key": intent.current.key } });
  }, onSuccess: () => { setConfirm(null); setReason(""); intent.current = null; refresh(); } });
  return <section className={styles.operation}><h3>会员处理</h3><p>人工补发记录为赠送会员，按月发放；已有会员时顺延生效。付费会员的停止与退款请在支付订单中办理。</p>{query.isError && <p role="alert">{errorText(query.error)}</p>}{query.data?.items.map(row => <p key={row.id}><strong>{row.plan_name}</strong> · {row.months} 个月 · 每月 {row.monthly_credits} 积分<small>{when(row.starts_at)} → {when(row.ends_at)} · {row.purchase_id ? `付费订单 ${row.purchase_id}` : "人工赠送"} · {row.cancelled_at ? "已停止" : "有效 / 待生效"}</small>{canAdjust && !row.purchase_id && !row.cancelled_at && <button disabled={reason.trim().length < 5 || mutation.isPending} onClick={() => setConfirm(row.id)}>停止并回收剩余赠送权益</button>}</p>)}{canAdjust && <><label>补发方案<select value={product} onChange={e => { setProduct(e.target.value); setConfirm(null); }}>{query.data?.products.map(p => <option key={p.id} value={p.id}>{p.name} · 每月 {p.credits} 积分</option>)}</select></label><label>处理原因<textarea minLength={5} maxLength={160} value={reason} onChange={e => { setReason(e.target.value); setConfirm(null); }} /></label><button disabled={reason.trim().length < 5 || mutation.isPending} onClick={() => setConfirm("grant")}>核对补发会员</button>{confirm && <div className={styles.confirm}><p>{confirm === "grant" ? `确认补发 ${query.data?.products.find(p => p.id === product)?.name}？` : "确认停止此赠送会员，并回收其全部剩余积分及未来发放？"} 原因：{reason}</p><button disabled={mutation.isPending} onClick={() => mutation.mutate(confirm)}>确认执行并记录</button></div>}</>}{mutation.isError && <p role="alert" className={styles.error}>{errorText(mutation.error)}</p>}{mutation.isSuccess && <p role="status">会员处理已保存。</p>}</section>;
}
