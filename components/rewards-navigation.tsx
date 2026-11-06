"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, CalendarCheck2, Check, Gift, LoaderCircle } from "lucide-react";
import Link from "@/components/seo-link";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { useCheckin, useRewards } from "@/features/billing/api/rewards";
import { requestLogin } from "@/lib/auth/session-events";
import { errorMessage } from "@/lib/api/request";
import styles from "./rewards-navigation.module.css";

export function RewardsNavigation() {
  const {workspace}=useWorkspace();
  const rewards=useRewards(workspace?.id);
  const claim=useCheckin(workspace?.id);
  const [open,setOpen]=useState(false);
  const root=useRef<HTMLDivElement>(null);
  const trigger=useRef<HTMLButtonElement>(null);
  useEffect(()=>{
    if(!open)return;
    const outside=(event:PointerEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false);};
    const key=(event:KeyboardEvent)=>{if(event.key==="Escape"){setOpen(false);trigger.current?.focus();}};
    document.addEventListener("pointerdown",outside);document.addEventListener("keydown",key);
    return()=>{document.removeEventListener("pointerdown",outside);document.removeEventListener("keydown",key);};
  },[open]);
  if(workspace&&rewards.data&&!rewards.data.checkin_enabled)return null;
  return <div className={styles.root} ref={root}>
    <button type="button" className={styles.trigger} ref={trigger} aria-label={rewards.data?.checked_in?"每日签到：今日已签到":"每日签到，免费领10积分"} aria-expanded={open} aria-controls="daily-checkin-panel" data-claimed={rewards.data?.checked_in||undefined} onClick={()=>workspace?setOpen(value=>!value):requestLogin("/account#rewards")}>
      {rewards.data?.checked_in?<Check size={17}/>:<CalendarCheck2 size={17}/>}<span>{rewards.data?.checked_in?"已签到":"签到"}</span>{!rewards.data?.checked_in&&<b>+10</b>}
    </button>
    {open&&<section className={styles.panel} id="daily-checkin-panel" aria-label="每日签到领积分">
      <div className={styles.intro}><span><Gift size={21}/></span><div><strong>每天来，免费领积分</strong><small>每日签到 · 创作补给</small></div></div>
      <div className={styles.amount}>+<strong>10</strong><span>积分</span></div>
      <button type="button" className={styles.claim} disabled={rewards.isPending||!rewards.data?.checkin_enabled||rewards.data?.checked_in||claim.isPending} onClick={()=>claim.mutate()}>{claim.isPending?<LoaderCircle size={17} className="spin"/>:rewards.data?.checked_in?<Check size={17}/>:null}{rewards.data?.checked_in?"今日已签到，明天再来":"立即领取"}</button>
      {claim.isError&&<p className={styles.error} role="alert">{errorMessage(claim.error)}</p>}
      {rewards.isError&&<button className={styles.retry} onClick={()=>void rewards.refetch()}>暂时无法读取，点击重试</button>}
      <div className={styles.footer}><span>到账后30天有效</span><Link href="/account#invitations" onClick={()=>setOpen(false)}>邀请返10%<ArrowUpRight size={13}/></Link></div>
    </section>}
  </div>;
}
