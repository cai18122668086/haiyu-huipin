"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, X } from "lucide-react";
import Link from "@/components/seo-link";
import { usePathname, useRouter } from "next/navigation";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { claimOfferImpression, newcomerOfferKey, useNewcomerOffer } from "@/features/billing/api/growth-center";
import { useBodyScrollLock } from "@/lib/hooks/use-body-scroll-lock";
import { newcomerCountdown } from "@/lib/newcomer-countdown";
import styles from "./newcomer-offer-controller.module.css";
import { newcomerDialogBlocks, newcomerReminderReady } from "@/lib/newcomer-offer-policy";

type Offer=Awaited<ReturnType<typeof claimOfferImpression>>;

export function NewcomerStageHint({ stage, freeAvailable = false }: { stage: "start" | "waiting"; freeAvailable?: boolean }) {
  const { workspace } = useWorkspace();
  const query = useNewcomerOffer(workspace?.id);
  if (!query.data?.eligible) return null;
  const data = query.data;
  return <aside className={styles.stageHint} aria-label="新客首单优惠">
    <span>{stage === "start" ? (freeAvailable ? "先免费生成你的第一套商品图" : "新客首单优惠，低价生成更多商品图") : "商品图正在生成，稍后查看效果"}</span>
    <Link href="/account?offer=newcomer#membership">首单 ¥{data.amount_fen / 100} · {data.credits}积分<ArrowUpRight size={14}/></Link>
  </aside>;
}

export function NewcomerOfferCountdown({ className }: { className?: string }) {
  const { workspace } = useWorkspace();
  const query = useNewcomerOffer(workspace?.id);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!query.data?.eligible || !query.data.window_ends_at) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [query.data?.eligible, query.data?.window_ends_at]);
  if (!query.data?.eligible || !query.data.window_ends_at) return null;
  const time = newcomerCountdown(query.data.window_ends_at, query.data.server_now, (now || query.dataUpdatedAt) - query.dataUpdatedAt);
  return <span className={className}>本轮优惠剩余 <time>{time.parts.join(":")}</time></span>;
}

export function NewcomerOfferController() {
  const { workspace } = useWorkspace();
  const pathname = usePathname();
  const router = useRouter();
  const query = useNewcomerOffer(workspace?.id);
  const cache = useQueryClient();
  const [tick, setTick] = useState(0);
  const [blocked, setBlocked] = useState(false);
  const [hiddenFloating, setHiddenFloating] = useState(false);
  const [offer, setOffer] = useState<{data:Offer;receivedAt:number;workspaceId:string}|null>(null);
  const inFlight = useRef(false), attempted = useRef(0);
  const protectedRoute = pathname.startsWith("/auth") || pathname.startsWith("/admin") || pathname.startsWith("/account");
  useEffect(() => {
    if (!query.data?.eligible) return;
    const timer = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [query.data?.eligible]);
  useEffect(() => {
    const data = query.data;
    if (!workspace?.id || protectedRoute || offer || inFlight.current || attempted.current === query.dataUpdatedAt || document.visibilityState !== "visible") return;
    if (!newcomerReminderReady(data, Date.now() - query.dataUpdatedAt)) return;
    if (Array.from(document.querySelectorAll('dialog[open], [role="dialog"], [aria-modal="true"]')).some(newcomerDialogBlocks)) return;
    // Wait five seconds after a preview/login/payment dialog closes, even if
    // the result timer became ready while the user was examining their image.
    const lastBlocked = document.documentElement.dataset.newcomerUnblockedAt;
    if (lastBlocked && Date.now() - Number(lastBlocked) < 5000) return;
    attempted.current = query.dataUpdatedAt;
    inFlight.current = true;
    const id = workspace.id;
    void claimOfferImpression(id).then(result => {
      cache.setQueryData(newcomerOfferKey(id), { ...result, should_show: false, reminded_today: result.should_show || result.reminded_today });
      if (result.should_show) setOffer({ data: result, receivedAt: Date.now(), workspaceId: id });
    }).catch(() => {}).finally(() => { inFlight.current = false; });
  }, [query.data, query.dataUpdatedAt, workspace?.id, tick, offer, cache, protectedRoute]);
  useEffect(() => {
    let blocked = false;
    const check = () => {
      const next = Array.from(document.querySelectorAll('dialog[open], [role="dialog"], [aria-modal="true"]')).some(newcomerDialogBlocks);
      if (blocked && !next) document.documentElement.dataset.newcomerUnblockedAt = String(Date.now());
      blocked = next;
      setBlocked(next);
    };
    const observer = new MutationObserver(check);
    observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["open", "role", "aria-modal", "aria-hidden", "hidden", "inert", "class", "style"] });
    check();
    const visibleAgain = () => {
      if (document.visibilityState === "visible") document.documentElement.dataset.newcomerUnblockedAt = String(Date.now());
    };
    document.addEventListener("visibilitychange", visibleAgain);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", visibleAgain); delete document.documentElement.dataset.newcomerUnblockedAt; };
  }, []);
  const visible = offer && offer.workspaceId === workspace?.id && query.data?.eligible && !protectedRoute;
  const floating = !hiddenFloating && !visible && !blocked && !protectedRoute && query.data?.eligible && query.data.trial_viewed;
  const floatingTime = query.data ? newcomerCountdown(query.data.window_ends_at, query.data.server_now, tick - query.dataUpdatedAt) : null;
  return <>
    {visible && <NewcomerOfferModal offer={offer.data} receivedAt={offer.receivedAt} now={tick || offer.receivedAt} onClose={() => setOffer(null)}/>}
    {floating && query.data && <NewcomerFloating amount={query.data.amount_fen / 100} parts={floatingTime?.parts ?? ["00", "00", "00"]} onClose={() => setHiddenFloating(true)} onOpen={() => {
      if (!workspace?.id || inFlight.current) return;
      const id = workspace.id;
      inFlight.current = true;
      void claimOfferImpression(id, true).then(result => {
        cache.setQueryData(newcomerOfferKey(id), {...result, should_show: false, reminded_today: result.should_show || result.reminded_today});
        if (result.should_show) setOffer({data: result, receivedAt: Date.now(), workspaceId: id});
      }).catch(() => router.push("/account?offer=newcomer#membership")).finally(() => { inFlight.current = false; });
    }}/>}
  </>;
}

export function NewcomerFloating({amount, parts, onOpen, onClose}: {amount:number;parts:readonly string[];onOpen:()=>void;onClose:()=>void}) {
  return <aside className={styles.floatingWrap} aria-label="新客福利悬浮入口">
    <button type="button" className={styles.floatingClose} aria-label="收起福利悬浮入口" onClick={onClose}><X size={15}/></button>
    <button type="button" className={styles.floating} aria-label={`查看限时福利，首单${amount}元`} onClick={onOpen}><strong>限时福利</strong><span className={styles.floatingPrice}><small>¥</small>{amount}</span><time aria-label={`优惠剩余${parts.join(":")}`}>{parts.join(":")}</time></button>
  </aside>;
}

export function NewcomerOfferModal({offer,receivedAt,now,onClose}:{offer:Offer;receivedAt:number;now:number;onClose:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null);
  useBodyScrollLock(true);
  useEffect(()=>{const element=dialog.current;if(element&&!element.open)element.showModal();return()=>element?.close();},[]);
  const time=newcomerCountdown(offer.window_ends_at,offer.server_now,now-receivedAt);
  useEffect(()=>{if(time.seconds===0)onClose();},[time.seconds,onClose]);
  return <dialog ref={dialog} className={styles.dialog} aria-labelledby="newcomer-offer-title" onCancel={event=>{event.preventDefault();onClose();}}>
    <header className={styles.top}>
      <button type="button" className={styles.close} aria-label="关闭新人优惠" onClick={onClose}><X size={24}/></button>
      <span className={styles.badge}>新客首单专享</span><h2 id="newcomer-offer-title">限时福利</h2>
    </header>
    <div className={styles.body}>
      <div className={styles.coupon}>
        <div className={styles.priceValue}><span>¥</span><strong>{(offer.amount_fen/100).toFixed(1)}</strong></div>
        <div className={styles.creditValue}><strong>{offer.credits}</strong><span>积分</span></div>
      </div>
      <div className={styles.countdown}><span>福利倒计时</span><div aria-label={`剩余${time.parts[0]}小时${time.parts[1]}分${time.parts[2]}秒`}>{time.parts.map((part,index)=><span key={index}>{index>0&&<i>:</i>}<b>{part}</b></span>)}</div></div>
      <Link className={styles.buy} href="/account?offer=newcomer#membership" onClick={onClose}>¥{offer.amount_fen/100} 立即抢购<ArrowUpRight size={21}/></Link>
      <div className={styles.bottom}><p>到账后{offer.validity_days}天有效 · 每人限购一次 · 无自动续费</p><details><summary>活动规则</summary><p>未付费用户首单专享，每人限购一次。每24小时更新一轮福利，未购买用户可继续参与；每天最多自动提醒一次。{offer.credits}积分到账后{offer.validity_days}天有效。</p></details></div>
    </div>
  </dialog>;
}
