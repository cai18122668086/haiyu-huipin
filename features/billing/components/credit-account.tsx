"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { CreditPurchasePanel } from "./credit-purchase";
import { Coins, ArrowUpRight, RefreshCw } from "lucide-react";
import { useCreditAccount, useCreditLedger } from "../api/credits";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import styles from "./credit-account.module.css";
import { BrandMark } from "@/components/brand-mark";

export function CreditAccountHeader() { return <header className={styles.accountHeader}><Link href="/" aria-label="返回创作工作台"><BrandMark /></Link><Link href="/account"><Coins size={16} />个人中心</Link></header>; }

export function CreditUsageNotice({ jobType, taskPreset }: { jobType: string; taskPreset?: string }) {
  const { workspace } = useWorkspace();
  const account = useCreditAccount(workspace?.id);
  if (!account.data?.enforcement_enabled) return null;
  const rule = account.data.pricing.find(p => p.job_type === jobType && (p.task_type ?? undefined) === taskPreset);
  return <div className={styles.notice}><Coins size={15} /><Link href="/account">可用 {account.data.available_credits.toLocaleString()} 积分</Link><span>{rule ? `${rule.credits_per_image} 积分 / 张 · 按成功出图结算` : "按实际生成图片结算"}</span>{!account.data.enforcement_enabled && <small>内部记账模式</small>}</div>;
}

export function CreditCostHint({ jobType, taskPreset, count, fallback }: { jobType: string; taskPreset?: string; count: number; fallback?: ReactNode }) {
  const { workspace } = useWorkspace();
  const account = useCreditAccount(workspace?.id);
  const rule = account.data?.pricing.find(p => p.job_type === jobType && (p.task_type ?? undefined) === taskPreset);
  if (!account.data?.enforcement_enabled || !rule || count <= 0) return fallback ?? null;
  const credits = rule.credits_per_image * count;
  return <span className={styles.costHint}>预计 {credits.toLocaleString()} 积分{account.data!.available_credits < credits && account.data!.enforcement_enabled ? <Link href="/account"> · 积分不足</Link> : null}</span>;
}

export function CreditAccountPanel() {
  const { workspace } = useWorkspace();
  const account = useCreditAccount(workspace?.id);
  const ledger = useCreditLedger(workspace?.id, Boolean(account.data?.enforcement_enabled));
  if (!account.data?.enforcement_enabled) return null;
  const labels: Record<string, string> = { reserve: "预留", settle: "结算", release: "释放", purchase: "充值到账" };
  return <section className={styles.panel} aria-labelledby="credit-account-heading"><div className={styles.heading}><div><p>CREATIVE CREDITS</p><h2 id="credit-account-heading">我的创作积分</h2></div><button aria-label="刷新积分余额" onClick={() => { void account.refetch(); void ledger.refetch(); }}><RefreshCw size={17} /></button></div>
    {account.isPending ? <p>正在读取积分账户…</p> : account.isError ? <p role="alert">积分账户读取失败，请重试。</p> : account.data ? <><div className={styles.summary}><div><span>可用积分</span><strong>{account.data.available_credits.toLocaleString()}</strong></div><div><span>任务预留</span><strong>{account.data.reserved_credits.toLocaleString()}</strong></div><div><span>账户总额</span><strong>{account.data.balance_credits.toLocaleString()}</strong></div></div><p className={styles.help}>提交任务时预留积分，按成功图片结算，未使用的预留自动释放。普通单图 5 积分/张，规划创作 15 积分/张。</p></> : null}
    {workspace && <CreditPurchasePanel key={workspace.id} workspaceId={workspace.id} />}
    <h3>最近积分明细</h3>{ledger.isError ? <p role="alert">积分明细读取失败。</p> : ledger.data?.items.length ? <div className={styles.table}><table><thead><tr><th>创作项目</th><th>事项</th><th>积分变动</th><th>释放预留</th><th>可用余额</th></tr></thead><tbody>{ledger.data.items.map(entry => <tr key={entry.id}><td>{entry.job_id ? <Link href={`/jobs?jobId=${encodeURIComponent(entry.job_id)}`}>{entry.title}<ArrowUpRight size={12} /></Link> : entry.title}<small>{new Date(entry.created_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</small></td><td>{labels[entry.entry_type] ?? entry.entry_type}</td><td>{entry.delta_balance_credits || (entry.entry_type === "reserve" ? `预留 ${entry.delta_reserved_credits}` : "0")}</td><td>{entry.released_credits ? `+${entry.released_credits}` : "—"}</td><td>{entry.available_after_credits.toLocaleString()}</td></tr>)}</tbody></table></div> : <p className={styles.empty}>{ledger.isPending ? "正在读取明细…" : "还没有积分明细，完成首次创作后会显示在这里。"}</p>}
  </section>;
}
