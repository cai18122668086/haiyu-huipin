"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Sparkles, TicketPercent, X } from "lucide-react";
import Link from "@/components/seo-link";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { claimOfferImpression, markTrialViewed, newcomerOfferKey, useNewcomerOffer } from "@/features/billing/api/growth-center";
import { useBodyScrollLock } from "@/lib/hooks/use-body-scroll-lock";
import { newcomerCountdown } from "@/lib/newcomer-countdown";
import styles from "./newcomer-offer-controller.module.css";

type Offer=Awaited<ReturnType<typeof claimOfferImpression>>;

export function TrialResultViewed({workspaceId,sessionId}:{workspaceId:string;sessionId:string}) {
  const cache=useQueryClient();
  useEffect(()=>{
    let active=true,recorded=false,inFlight=false;
    async function observe(){
      if(!active||recorded||inFlight||document.visibilityState!=="visible")return;
      inFlight=true;
      try{const result=await markTrialViewed(workspaceId,sessionId);recorded=true;if(active)cache.setQueryData(newcomerOfferKey(workspaceId),result);}catch{/* Promotion errors never interrupt image delivery. */}finally{inFlight=false;}
    }
    void observe();
    const timer=setInterval(()=>void observe(),30_000);
    const visible=()=>void observe();document.addEventListener("visibilitychange",visible);
    return()=>{active=false;clearInterval(timer);document.removeEventListener("visibilitychange",visible);};
  },[workspaceId,sessionId,cache]);
  return null;
}

export function NewcomerOfferController(){
  const {workspace}=useWorkspace();
  const query=useNewcomerOffer(workspace?.id);
  const cache=useQueryClient();
  const [tick,setTick]=useState(0);
  const [offer,setOffer]=useState<{data:Offer;receivedAt:number;workspaceId:string}|null>(null);
  const inFlight=useRef(false), attempted=useRef(0);
  useEffect(()=>{
    if(!query.data?.eligible)return;
    const timer=setInterval(()=>setTick(Date.now()),1000);
    return()=>clearInterval(timer);
  },[query.data?.eligible]);
  useEffect(()=>{
    const data=query.data;
    if(!workspace?.id||!data?.should_show||!data.ready_at||offer||inFlight.current||attempted.current===query.dataUpdatedAt||document.visibilityState!=="visible"||document.querySelector('dialog[open], [role="dialog"], [aria-modal="true"]'))return;
    const serverTime=Date.parse(data.server_now)+Math.max(0,Date.now()-query.dataUpdatedAt);
    if(serverTime<Date.parse(data.ready_at))return;
    attempted.current=query.dataUpdatedAt;inFlight.current=true;
    const id=workspace.id;
    void claimOfferImpression(id).then(result=>{
      cache.setQueryData(newcomerOfferKey(id),{...result,should_show:false});
      if(result.should_show)setOffer({data:result,receivedAt:Date.now(),workspaceId:id});
    }).catch(()=>{/* Retry on the next server refresh, never interrupt work. */}).finally(()=>{inFlight.current=false;});
  },[query.data,query.dataUpdatedAt,workspace?.id,tick,offer,cache]);
  const visible=offer&&offer.workspaceId===workspace?.id&&query.data?.eligible;
  return visible?<NewcomerOfferModal offer={offer.data} receivedAt={offer.receivedAt} now={tick||offer.receivedAt} onClose={()=>setOffer(null)}/>:null;
}

export function NewcomerOfferModal({offer,receivedAt,now,onClose}:{offer:Offer;receivedAt:number;now:number;onClose:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null);
  useBodyScrollLock(true);
  useEffect(()=>{const element=dialog.current;if(element&&!element.open)element.showModal();return()=>element?.close();},[]);
  const time=newcomerCountdown(offer.window_ends_at,offer.server_now,now-receivedAt);
  useEffect(()=>{if(time.seconds===0)onClose();},[time.seconds,onClose]);
  return <dialog ref={dialog} className={styles.dialog} aria-labelledby="newcomer-offer-title" onCancel={event=>{event.preventDefault();onClose();}} onClick={event=>{if(event.target===event.currentTarget){const r=event.currentTarget.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)onClose();}}}>
    <button type="button" className={styles.close} aria-label="关闭新人优惠" onClick={onClose}><X size={20}/></button>
    <div className={styles.top}><span className={styles.badge}><TicketPercent size={15}/>新客每日专享</span><Sparkles className={styles.sparkle} size={38}/><h2 id="newcomer-offer-title">好图已到手<br/><em>下一套，放开画。</em></h2><p>首单体验价 · 让灵感继续变成生意</p></div>
    <div className={styles.coupon}><div className={styles.price}><span>¥</span><strong>{(offer.amount_fen/100).toFixed(1)}</strong><b>首单特惠</b></div><div className={styles.benefit}><strong>{offer.credits}<small>积分</small></strong><span>快速版可出 <b>{offer.fast_images}</b> 张</span></div><div className={styles.perImage}>算下来，每张不到 <strong>0.5</strong> 元</div></div>
    <div className={styles.countdown}><span>本轮福利倒计时</span><div aria-label={`剩余${time.parts[0]}小时${time.parts[1]}分${time.parts[2]}秒`}>{time.parts.map((part,index)=><span key={index}>{index>0&&<i>:</i>}<b>{part}</b></span>)}</div></div>
    <Link className={styles.buy} href="/account?offer=newcomer#membership" onClick={onClose}>¥9.9 立即开画<ArrowUpRight size={20}/></Link>
    <div className={styles.bottom}><span>一次购买 · 无自动续费</span><details><summary>活动规则</summary><p>未付费用户首单专享，每人限购一次。每24小时更新一轮福利，未购买用户可继续参与；每天最多提醒一次。160积分到账后30天有效，“20张”按快速版8积分/张计算。</p></details></div>
  </dialog>;
}
