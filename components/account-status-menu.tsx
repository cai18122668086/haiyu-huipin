"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ChevronDown, Coins, Crown, RefreshCw, UserRound } from "lucide-react";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { authUserLabel, useCurrentAuthUser } from "@/features/auth/api/me";
import { useCreditAccount } from "@/features/billing/api/credits";
import { useMembership } from "@/features/billing/api/account-center";
import styles from "./account-status-menu.module.css";

const day = (value?: string | null) => value ? new Date(value).toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai" }) : "";
const number = (value: number) => value.toLocaleString("zh-CN");

export function AccountStatusMenu() {
  const { workspace } = useWorkspace();
  const user = useCurrentAuthUser();
  const wallet = useCreditAccount(workspace?.id);
  const member = useMembership(workspace?.id);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => { if (!ref.current?.contains(event.target as Node)) setOpen(false); };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener("pointerdown", close); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", key); };
  }, [open]);
  const plan = member.data?.plan_name;
  const pending = member.data?.next_plan_name;
  const identity = plan ? `${plan}会员` : pending ? "会员待生效" : member.data?.membership_status === "expired" ? "会员已到期" : "创作账户";
  return <div ref={ref} className={styles.root}>
    <button ref={trigger} type="button" className={styles.trigger} data-member={Boolean(plan) || undefined} aria-expanded={open} aria-controls="huipin-account-summary" onClick={() => setOpen(value => !value)}>
      <span className={styles.identity}>{plan ? <Crown size={16} /> : <UserRound size={16} />}<span>{identity}</span></span>
      <span className={styles.balance}><Coins size={14} />{wallet.data ? `${number(wallet.data.available_credits)} 积分` : wallet.isError ? "积分待同步" : "读取积分…"}</span><ChevronDown size={13} />
    </button>
    {open && <section id="huipin-account-summary" className={styles.panel} aria-label="会员与积分摘要">
      <div className={styles.heading}><strong>{authUserLabel(user.data)}</strong><span>{identity}</span></div>
      {member.data?.previous_plan_name && !plan && <p>{member.data.previous_plan_name}会员已到期，可查看权益与续费方案。</p>}
      {plan && <p>会员有效至 {day(member.data?.ends_at)}</p>}
      {pending && <p>{pending}将于 {day(member.data?.next_plan_starts_at)} 生效</p>}
      {member.isError && <button onClick={() => void member.refetch()}><RefreshCw size={14} />重新读取会员信息</button>}
      {wallet.data ? <dl className={styles.wallet}><div><dt>可用积分</dt><dd>{number(wallet.data.available_credits)}</dd></div><div><dt>任务预留</dt><dd>{number(wallet.data.reserved_credits)}</dd></div></dl> : <button onClick={() => void wallet.refetch()}><RefreshCw size={14} />{wallet.isError ? "积分读取失败，点击重试" : "正在同步积分"}</button>}
      {member.data?.monthly_credits ? <p>本期会员额度 {number(member.data.monthly_credits)} 积分 / 月 · 全部创作工具</p> : null}
      {member.data?.next_grant_at && <p>下期发放：{day(member.data.next_grant_at)}</p>}
      {!!member.data?.expiring_credits && <p className={styles.expiry}>{number(member.data.expiring_credits)} 积分将于 {day(member.data.next_expiry_at)} 到期</p>}
      <p>任务预留在结算后更新，未使用部分自动释放。</p>
      <nav onClick={() => setOpen(false)} aria-label="账户快捷导航">
        <Link href="/account#membership">{plan ? "管理会员与权益" : "会员与充值"}</Link><Link href="/account#credits">积分明细</Link>
        <Link href="/jobs">全部生成记录</Link><Link href="/assets">我的素材</Link><Link href="/account#orders">订单与发票</Link><Link href="/account#settings">账户设置</Link><Link href="/account#help">消息与帮助</Link>
      </nav>
    </section>}
  </div>;
}
