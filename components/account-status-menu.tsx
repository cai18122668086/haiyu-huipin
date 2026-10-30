"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, ChevronDown, Coins, Crown, Images, RefreshCw, UserRound } from "lucide-react";
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
      <span className={styles.identity}>{plan ? <Crown size={14} /> : <UserRound size={14} />}<span>{identity}</span></span>
      <span className={styles.balance}><Coins size={14} />{wallet.data ? <><b>{number(wallet.data.available_credits)}</b><span>积分</span></> : wallet.isError ? "待同步" : "同步中"}</span><ChevronDown size={12} />
    </button>
    {open && <section id="huipin-account-summary" className={styles.panel} aria-label="会员与积分摘要">
      <div className={styles.memberCard}>
        <div className={styles.heading}>
          <span className={styles.memberIcon}>{plan ? <Crown size={21} /> : <UserRound size={21} />}</span>
          <div><strong>{identity}</strong><small>{authUserLabel(user.data)}</small></div>
          <span className={styles.status}>{plan ? "权益生效中" : pending ? "待生效" : "我的账户"}</span>
        </div>
        {plan && <p className={styles.validity}>{day(member.data?.ends_at)} 到期</p>}
        {pending && <p className={styles.validity}>{pending} · {day(member.data?.next_plan_starts_at)} 生效</p>}
        {member.data?.previous_plan_name && !plan && !pending && <p className={styles.validity}>{member.data.previous_plan_name}已到期</p>}
      </div>
      <div className={styles.creditSection}>
        {wallet.data ? <dl className={styles.wallet}>
          <div><dt>可用积分</dt><dd>{number(wallet.data.available_credits)}</dd></div>
          <div><dt>任务预留</dt><dd>{number(wallet.data.reserved_credits)}</dd></div>
        </dl> : <button className={styles.retry} onClick={() => void wallet.refetch()}><RefreshCw size={14} />{wallet.isError ? "积分同步失败，重试" : "正在同步积分"}</button>}
        {member.isError && <button className={styles.retry} onClick={() => void member.refetch()}><RefreshCw size={14} />会员信息同步失败，重试</button>}
        <div className={styles.primaryActions} onClick={() => setOpen(false)}>
          <Link href="/account#membership">{plan ? "管理会员" : "开通会员"}<ArrowUpRight size={14} /></Link>
          <Link href="/account#credits">积分明细</Link>
        </div>
        {(!!member.data?.monthly_credits || !!member.data?.expiring_credits) && <details className={styles.entitlements}>
          <summary>额度与有效期<ChevronDown size={13} /></summary>
          <dl>
            {!!member.data?.monthly_credits && <div><dt>每月额度</dt><dd>{number(member.data.monthly_credits)} 积分</dd></div>}
            {member.data?.next_grant_at && <div><dt>下期发放</dt><dd>{day(member.data.next_grant_at)}</dd></div>}
            {!!member.data?.expiring_credits && <div><dt>{day(member.data.next_expiry_at)} 到期</dt><dd>{number(member.data.expiring_credits)} 积分</dd></div>}
          </dl>
          <p>任务未使用的预留积分会自动释放。</p>
        </details>}
      </div>
      <nav className={styles.shortcuts} onClick={() => setOpen(false)} aria-label="账户快捷导航">
        <Link href="/assets"><Images size={17} /><span>我的素材</span><ArrowUpRight size={13} /></Link>
      </nav>
      <nav className={styles.footer} onClick={() => setOpen(false)} aria-label="账户设置与帮助">
        <Link href="/account#orders">订单与发票</Link><Link href="/account#settings">账户设置</Link><Link href="/account#help">帮助中心</Link>
      </nav>
    </section>}
  </div>;
}
