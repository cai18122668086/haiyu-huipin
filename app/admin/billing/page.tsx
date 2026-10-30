"use client";
import { AdminSupportCases } from "@/components/support-cases";
import { useSearchParams } from "next/navigation";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AdminShell } from "@/components/admin/admin-shell";
import { getAdminAccess } from "@/features/admin/api/admin";
import { request, errorText, exportOrders } from "./billing-api";
import { FinanceOverview, MembershipTools, RefundDialog, RefundRecords } from "./finance-tools";
import { formatBeijingDate } from "@/lib/datetime.mjs";
import styles from "./page.module.css";

type User = { id: string; name: string; phone_hint: string | null; status: string; workspace_ids: string[]; created_at: string };
type Order = { id: string; workspace_id: string; user_id: string; package_name: string; amount_fen: number; refunded_amount_fen: number; credits: number; status: string; channel: string; gateway_trade_no: string | null; created_at: string; reconciliation: { attempts: number; last_at: string | null; next_at: string | null; provider_status: string | null; last_error: string | null } };
type Audit = { id: number; action: string; actor_user_id: string | null; workspace_id: string | null; resource_id: string | null; details: Record<string, unknown>; occurred_at: string };
type Detail = { workspace_id: string; name: string; balance_credits: number; reserved_credits: number; available_credits: number; membership: { plan_name: string | null; monthly_credits: number; ends_at: string | null; next_grant_at: string | null }; ledger: { id: number; delta: number; reserved_delta: number; title: string; created_at: string }[] };

const when = (value: string | null) => value ? formatBeijingDate(value, { dateStyle: "medium", timeStyle: "short" }) : "—";
const label = (value: string) => ({ pending: "待付款", paid: "已到账", partially_refunded: "部分退款", refunded: "已退款", closed: "已关闭", active: "正常", suspended: "已停用", alipay: "支付宝", wechat: "微信支付" })[value] ?? value;

export default function BillingAdminPage() {
  const access = useQuery({ queryKey: ["admin", "access"], queryFn: ({ signal }) => getAdminAccess(signal), retry: false });
  return <AdminShell><main className={styles.page}>
    {access.isPending ? <p>正在确认管理权限…</p> : !access.data?.can_manage ? <p role="alert">当前账户没有管理权限。</p> : <SupportConsole />}
  </main></AdminShell>;
}

function SupportConsole() {
  const client = useQueryClient();
  const params = useSearchParams();
  const [tab, setTab] = useState<"users" | "orders" | "audit" | "refunds">("users");
  const [refundOrder, setRefundOrder] = useState<Order | null>(null);
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [needsReview, setNeedsReview] = useState(false);
  const [status, setStatus] = useState("");
  const [workspace, setWorkspace] = useState<string | null>(params.get("workspaceId"));
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [delta, setDelta] = useState("");
  const [reason, setReason] = useState(params.get("supportCaseId") ? `售后补偿 · ${params.get("supportCaseId")} · 任务 ${params.get("jobId") ?? "未关联"}` : "");
  const [confirm, setConfirm] = useState<"adjust" | "status" | null>(null);
  const intent = useRef<{ fingerprint: string; key: string } | null>(null);
  const capability = useQuery({ queryKey: ["support", "access"], queryFn: () => request<{ can_adjust: boolean; user_id: string }>("/access"), retry: false });
  const users = useQuery({ queryKey: ["support", "users", search, offset], enabled: tab === "users", queryFn: () => request<{ items: User[]; total: number }>(`/users?search=${encodeURIComponent(search)}&offset=${offset}`) });
  const orders = useQuery({ queryKey: ["support", "orders", search, status, offset, needsReview], enabled: tab === "orders", queryFn: () => request<{ items: Order[]; total: number }>(`/orders?search=${encodeURIComponent(search)}&offset=${offset}${status ? `&status=${status}` : ""}&needs_review=${needsReview}`) });
  const audit = useQuery({ queryKey: ["support", "audit", offset], enabled: tab === "audit", queryFn: () => request<{ items: Audit[]; total: number }>(`/audit?offset=${offset}`) });
  const detail = useQuery({ queryKey: ["support", "detail", workspace], enabled: Boolean(workspace), queryFn: () => request<Detail>(`/workspaces/${encodeURIComponent(workspace!)}`) });
  function refresh() { void client.invalidateQueries({ queryKey: ["support"] }); }
  const adjustment = useMutation({ mutationFn: () => {
    const payload = JSON.stringify({ delta: Number(delta), reason });
    const fingerprint = workspace + payload;
    if (intent.current?.fingerprint !== fingerprint) intent.current = { fingerprint, key: crypto.randomUUID() };
    return request(`/workspaces/${encodeURIComponent(workspace!)}/adjustments`, { method: "POST", body: payload, headers: { "Idempotency-Key": intent.current.key } });
  }, onSuccess: () => { setConfirm(null); setDelta(""); setReason(""); intent.current = null; refresh(); } });
  const changeStatus = useMutation({ mutationFn: () => request(`/users/${encodeURIComponent(selectedUser!.id)}/status`, { method: "PATCH", body: JSON.stringify({ status: selectedUser!.status === "active" ? "suspended" : "active", reason }) }), onSuccess: () => { setConfirm(null); setReason(""); setSelectedUser(null); refresh(); } });
  const reconcile = useMutation({ mutationFn: (id: string) => request<{ status: string }>(`/orders/${encodeURIComponent(id)}/reconcile`, { method: "POST" }), onSuccess: refresh });
  const exportData = useMutation({ mutationFn: exportOrders });
  const count = tab === "users" ? users.data?.total : tab === "orders" ? orders.data?.total : audit.data?.total;
  const loading = tab === "users" ? users.isPending : tab === "orders" ? orders.isPending : audit.isPending;
  const failure = tab === "users" ? users.error : tab === "orders" ? orders.error : audit.error;
  return <>
    <header className={styles.heading}><div><p>海宇绘品 · 管理中心</p><h1>用户与财务</h1><p>查询用户、核对收款与处理积分，所有人工变动保留记录。</p></div><button onClick={refresh}>刷新数据</button></header>
    <FinanceOverview />
    <nav className={styles.tabs} aria-label="用户与财务管理">{([ ["users", "用户处理"], ["orders", "支付订单"], ["refunds", "退款记录"], ["audit", "操作记录"] ] as const).map(([key, name]) => <button key={key} aria-pressed={tab === key} onClick={() => { setTab(key); setOffset(0); setSearch(""); setDraft(""); }}>{name}</button>)}</nav>
    {tab === "refunds" ? <RefundRecords canAdjust={Boolean(capability.data?.can_adjust)} refresh={refresh} /> : <>
    <form className={styles.toolbar} onSubmit={event => { event.preventDefault(); setSearch(draft.trim()); setOffset(0); }}>
      {tab !== "audit" && <><input aria-label="查询用户或订单" value={draft} onChange={event => setDraft(event.target.value)} placeholder={tab === "users" ? "手机号、用户 ID 或姓名" : "订单号、支付流水或用户 ID"} /><button>查询</button></>}
      {tab === "orders" && <select aria-label="订单状态" value={status} onChange={event => { setStatus(event.target.value); setOffset(0); }}><option value="">全部状态</option><option value="pending">待付款</option><option value="paid">已到账</option><option value="partially_refunded">部分退款</option><option value="refunded">已退款</option><option value="closed">已关闭</option></select>}
      {tab === "orders" && <label><input type="checkbox" checked={needsReview} onChange={e => { setNeedsReview(e.target.checked); setOffset(0); }} /> 仅看补单异常</label>}
      {tab === "orders" && <button type="button" disabled={exportData.isPending} onClick={() => exportData.mutate()}>导出全部订单 CSV</button>}
      <span>共 {count ?? "—"} 条</span>
    </form>
    {failure && <p className={styles.error} role="alert">{errorText(failure)}</p>}
    {loading ? <p>正在加载…</p> : <div className={styles.tableWrap}><table>
      {tab === "users" && <><thead><tr><th>用户</th><th>手机号</th><th>状态</th><th>注册时间</th><th>处理</th></tr></thead><tbody>{users.data?.items.map(user => <tr key={user.id}><td>{user.name}<small>{user.id}</small></td><td>{user.phone_hint ?? "未绑定"}</td><td>{label(user.status)}</td><td>{when(user.created_at)}</td><td>{user.workspace_ids.map(id => <button key={id} onClick={() => { setWorkspace(id); setSelectedUser(user); setConfirm(null); adjustment.reset(); changeStatus.reset(); }}>账户详情</button>)}</td></tr>)}</tbody></>}
      {tab === "orders" && <><thead><tr><th>订单</th><th>用户 / 工作区</th><th>金额</th><th>渠道 / 状态</th><th>操作</th></tr></thead><tbody>{orders.data?.items.map(order => <tr key={order.id}><td>{order.package_name}<small>{order.id}</small><small>{when(order.created_at)}</small></td><td><small>{order.user_id}</small><small>{order.workspace_id}</small></td><td>¥{(order.amount_fen / 100).toFixed(2)}<small>{order.credits.toLocaleString()} 积分</small></td><td>{label(order.channel)} · {label(order.status)}<small>{order.gateway_trade_no ?? "尚无支付流水"}</small>{order.status === "pending" && <><small>已查单 {order.reconciliation.attempts} 次 · {order.reconciliation.provider_status ?? "等待核对"}</small><small>上次 {when(order.reconciliation.last_at)} · 下次 {when(order.reconciliation.next_at)}</small>{order.reconciliation.last_error && <small role="alert">需核对：{order.reconciliation.last_error}</small>}</>}</td><td><button disabled={!capability.data?.can_adjust || reconcile.isPending || order.status !== "pending"} onClick={() => reconcile.mutate(order.id)}>向渠道核对到账</button>{["paid", "partially_refunded"].includes(order.status) && order.refunded_amount_fen < order.amount_fen && <button disabled={!capability.data?.can_adjust} onClick={() => setRefundOrder(order)}>原路退款</button>}<small>已退 ¥{(order.refunded_amount_fen / 100).toFixed(2)}</small></td></tr>)}</tbody></>}
      {tab === "audit" && <><thead><tr><th>时间 / 操作人</th><th>事项</th><th>关联记录</th><th>说明</th></tr></thead><tbody>{audit.data?.items.map(row => <tr key={row.id}><td>{when(row.occurred_at)}<small>{row.actor_user_id ?? "系统"}</small></td><td>{({ "credits.admin_adjusted": "人工积分调整", "credits.admin_reconciled": "人工核对收款", "user.support_status_changed": "用户状态变更", "credits.purchase_settled": "支付到账", "credits.refund_requested": "发起原路退款", "credits.refund_succeeded": "原路退款成功", "credits.refund_failed": "退款未成功", "membership.admin_granted": "补发会员", "membership.admin_cancelled": "停止赠送会员" })[row.action] ?? row.action}</td><td><small>{row.workspace_id}</small><small>{row.resource_id}</small></td><td>{String(row.details.reason ?? "")}<small>{row.details.delta !== undefined ? `积分变动 ${row.details.delta}，余额 ${row.details.before} → ${row.details.after}` : row.details.amount_fen !== undefined ? `收款 ¥${(Number(row.details.amount_fen) / 100).toFixed(2)}` : ""}</small></td></tr>)}</tbody></>}
    </table>{count === 0 && <p className={styles.empty}>没有符合条件的记录</p>}</div>}
    {exportData.isError && <p className={styles.error} role="alert">{errorText(exportData.error)}</p>}
    {reconcile.isError && <p className={styles.error} role="alert">{errorText(reconcile.error)}</p>}
    {reconcile.isSuccess && <p role="status">{reconcile.data.status === "paid" ? "渠道已确认，积分与会员已到账。" : "渠道尚未确认付款，请勿手工标记已支付。"}</p>}
    <footer className={styles.toolbar}><button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button><span>第 {Math.floor(offset / 30) + 1} 页</span><button disabled={offset + 30 >= (count ?? 0)} onClick={() => setOffset(offset + 30)}>下一页</button></footer>
    </>}
    <AdminSupportCases />
    {refundOrder && <RefundDialog key={refundOrder.id} order={refundOrder} close={() => setRefundOrder(null)} refresh={refresh} />}
    {workspace && <div className={styles.overlay}><section className={styles.dialog} role="dialog" aria-modal="true" aria-label="用户账户详情"><header className={styles.heading}><h2>用户账户详情</h2><button onClick={() => { setWorkspace(null); setConfirm(null); }}>关闭</button></header>
      <p>{selectedUser?.name} · {selectedUser?.phone_hint}<small className={styles.id}>{workspace}</small></p>
      {detail.isPending ? <p>正在读取账户…</p> : detail.isError ? <p role="alert">{errorText(detail.error)}</p> : detail.data && <>
        <div className={styles.stats}><div>可用积分<strong>{detail.data.available_credits.toLocaleString()}</strong></div><div>冻结积分<strong>{detail.data.reserved_credits.toLocaleString()}</strong></div><div>会员<strong>{detail.data.membership.plan_name ?? "未开通"}</strong></div></div>
        <p>会员到期：{when(detail.data.membership.ends_at)} · 下次发放：{when(detail.data.membership.next_grant_at)}</p>
        {capability.data?.can_adjust && <form className={styles.operation} onSubmit={event => { event.preventDefault(); adjustment.reset(); setConfirm("adjust"); }}><h3>积分处理</h3><p>正数补发，负数扣回；冻结与已消费积分不能扣回。补发积分无固定到期日。</p><label>积分变动<input required type="number" step="1" min="-1000000" max="1000000" value={delta} onChange={event => { setDelta(event.target.value); setConfirm(null); }} /></label><label>处理原因<textarea required minLength={5} maxLength={180} value={reason} onChange={event => { setReason(event.target.value); setConfirm(null); }} /></label><button disabled={!Number.isInteger(Number(delta)) || Number(delta) === 0}>核对本次调整</button>
          {confirm === "adjust" && <div className={styles.confirm}><p>确认对 {selectedUser?.name} {Number(delta) > 0 ? "补发" : "扣回"} <strong>{Math.abs(Number(delta))} 积分</strong>，原因：{reason}</p><button type="button" disabled={adjustment.isPending} onClick={() => adjustment.mutate()}>{adjustment.isPending ? "正在保存…" : "确认执行并记录"}</button></div>}
        </form>}
        {capability.data?.can_adjust && selectedUser && selectedUser.id !== capability.data.user_id && <div className={styles.operation}><h3>账户状态</h3><p>当前状态：{label(selectedUser.status)}。处理原因使用上方填写内容。</p><button disabled={reason.trim().length < 5} onClick={() => setConfirm("status")}>{selectedUser.status === "active" ? "停用此用户" : "恢复此用户"}</button>{confirm === "status" && <div className={styles.confirm}><p>确认{selectedUser.status === "active" ? "停用" : "恢复"} {selectedUser.name}？</p><button disabled={changeStatus.isPending} onClick={() => changeStatus.mutate()}>确认状态变更</button></div>}</div>}
        {(adjustment.isError || changeStatus.isError) && <p className={styles.error} role="alert">{errorText(adjustment.error ?? changeStatus.error)}</p>}
        {adjustment.isSuccess && <p role="status">积分处理已保存。</p>}
        <MembershipTools key={workspace} workspace={workspace} canAdjust={Boolean(capability.data?.can_adjust)} refresh={refresh} />
        <h3>最近积分记录</h3><div className={styles.tableWrap}><table><thead><tr><th>事项</th><th>余额变动</th><th>冻结变动</th><th>时间</th></tr></thead><tbody>{detail.data.ledger.map(row => <tr key={row.id}><td>{row.title}</td><td>{row.delta > 0 ? "+" : ""}{row.delta}</td><td>{row.reserved_delta}</td><td>{when(row.created_at)}</td></tr>)}</tbody></table></div>
      </>}
    </section></div>}
  </>;
}
