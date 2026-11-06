"use client";

import { useState } from "react";
import Link from "@/components/seo-link";
import { ArrowUpRight, CalendarCheck2, Check, Gift, LoaderCircle, UsersRound } from "lucide-react";
import { useCheckin, useRewards } from "@/features/billing/api/rewards";
import { errorMessage } from "@/lib/api/request";
import styles from "./rewards-center.module.css";

const number = (value: number) => value.toLocaleString("zh-CN");
const date = (value: string) => new Date(value).toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit" });

export function RewardsCenter({ workspaceId }: { workspaceId: string }) {
  const rewards = useRewards(workspaceId);
  const claim = useCheckin(workspaceId);
  const [message, setMessage] = useState("");
  const data = rewards.data;

  async function checkin() {
    setMessage("");
    try {
      const result = await claim.mutateAsync();
      setMessage(result.created ? result.adjusted_credits ? `签到完成 · ${result.credited_credits} 积分到账` : `${result.credited_credits} 积分已到账` : "今日已签到");
    } catch (error) { setMessage(errorMessage(error)); }
  }

  if (rewards.isPending) return <div className={`page-content ${styles.root}`} aria-busy="true"><div className={styles.cards}><div className={styles.skeleton} /><div className={styles.skeleton} /></div></div>;
  if (rewards.isError) return <div className={`page-content ${styles.root}`} role="alert"><p>福利暂时无法读取。</p><button className={styles.action} onClick={() => void rewards.refetch()}>重新加载</button></div>;
  if (!data?.checkin_enabled && !data?.referral_enabled) return <div className="page-content"><Link href="/account">返回账户概览<ArrowUpRight size={15} /></Link></div>;

  return <div className={`page-content ${styles.root}`}>
    <div className={styles.cards}>
      {data.checkin_enabled && <section className={styles.card} aria-label="每日签到">
        <header><span className={styles.icon}><CalendarCheck2 size={22} /></span><h2>每日签到</h2></header>
        <div className={styles.amount}><strong>{number(data.daily_credits)}</strong><span>积分</span></div>
        <p className={styles.description}>北京时间每天一次</p>
        <button type="button" className={styles.action} data-claimed={data.checked_in || undefined} disabled={data.checked_in || claim.isPending} onClick={() => void checkin()}>
          {claim.isPending ? <><LoaderCircle className="spin" size={17} />领取中</> : data.checked_in ? <><Check size={17} />今日已签到</> : <>立即领取<ArrowUpRight size={17} /></>}
        </button>
      </section>}
      {data.referral_enabled && <section className={`${styles.card} ${styles.invitation}`} aria-label="邀请好友">
        <header><span className={styles.icon}><UsersRound size={22} /></span><h2>邀请好友</h2></header>
        <div className={styles.amount}><strong>{data.reward_percent}<small>%</small></strong><span>返赠积分</span></div>
        <p className={styles.description}>积分包与会员购买，均可获得奖励</p>
        <div className={styles.statistics}><span>已邀请 <b>{number(data.invited_count)}</b> 人</span><span>累计奖励 <b>{number(data.earned_credits)}</b> 积分</span></div>
        <Link href="/account#invitations" className={styles.action}>进入邀请中心<ArrowUpRight size={17} /></Link>
      </section>}
    </div>
    <div className={styles.footnote}>
      <span>赠送积分 {data.validity_days} 天有效</span>
      <details><summary>奖励规则</summary><div>签到按北京时间每天领取一次。邀请仅限直接邀请的新注册好友为自己的个人账户充值；会员按每期实际到账积分返赠，充值退款会同步调整对应奖励。</div></details>
    </div>
    {message && <p className={styles.feedback} role="status">{message}</p>}
    {data.pending_adjustment_credits > 0 && <p className={styles.adjustment} title="充值退款对应的已用邀请奖励，将由后续赠送积分抵扣。">待调整 {number(data.pending_adjustment_credits)} 积分 · <Link href="/account#credits">查看明细</Link></p>}
    {data.recent.length > 0 && <section className={styles.history} aria-label="最近奖励">
      <header><h2><Gift size={17} />最近奖励</h2><Link href="/account#credits">全部明细<ArrowUpRight size={14} /></Link></header>
      <ul>{data.recent.map((event, index) => <li key={`${event.created_at}-${event.kind}-${index}`}><span className={styles.eventIcon}>{event.kind === "checkin" ? <CalendarCheck2 size={18} /> : <UsersRound size={18} />}</span><div><strong>{event.title}</strong><small>{date(event.created_at)}{event.adjusted && " · 已调整"}</small></div><b>+{number(event.credits)}<small>积分</small></b></li>)}</ul>
    </section>}
  </div>;
}
