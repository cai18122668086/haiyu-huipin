"use client";

import { useState } from "react";
import { ArrowUpRight, Check, ChevronLeft, ChevronRight, Copy, Gift, UsersRound } from "lucide-react";
import { useInvitationCenter, type InvitationFilter } from "@/features/billing/api/growth-center";
import styles from "./invitation-center.module.css";

const number=(value:number)=>value.toLocaleString("zh-CN");
const day=(value:string)=>new Date(value).toLocaleDateString("zh-CN",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit"});
const labels={paid:"已充值",unpaid:"未充值",refunded:"已退款"};

export function InvitationCenter({workspaceId}:{workspaceId:string}){
  const [filter,setFilter]=useState<InvitationFilter>("all"),[offset,setOffset]=useState(0),[copied,setCopied]=useState<"code"|"link"|null>(null),[fallback,setFallback]=useState<string>();
  const query=useInvitationCenter(workspaceId,filter,offset),data=query.data;
  async function copy(kind:"code"|"link"){
    const value=kind==="code"?data?.invite_code:data?.invite_url;if(!value)return;
    try{await navigator.clipboard.writeText(value);setCopied(kind);setFallback(undefined);}catch{setFallback(value);}
  }
  if(query.isPending)return <div className={`page-content ${styles.root}`} aria-busy="true"><div className={styles.skeleton}/></div>;
  if(query.isError||!data)return <div className={`page-content ${styles.root}`} role="alert"><p>邀请记录暂时无法读取。</p><button className={styles.share} onClick={()=>void query.refetch()}>重新加载</button></div>;
  return <div className={`page-content ${styles.root}`}>
    <section className={styles.hero} aria-label="邀请好友返积分"><div className={styles.pitch}><span className={styles.eyebrow}><Gift size={15}/>邀请好友 · 创作加倍</span><h2><span>好友充值，</span><span>你收积分。</span></h2><p>积分包、会员均参与，好友每次到账都为你带来奖励。</p><div className={styles.inviteActions}><button type="button" className={styles.code} onClick={()=>void copy("code")} aria-label="复制我的邀请码"><span>邀请码</span><b>{data.invite_code}</b>{copied==="code"?<Check size={15}/>:<Copy size={15}/>}</button><button type="button" className={styles.share} onClick={()=>void copy("link")}>{copied==="link"?<Check size={17}/>:<ArrowUpRight size={17}/>} {copied==="link"?"链接已复制":"复制邀请链接"}</button></div></div><div className={styles.rate}><strong>{data.reward_percent}<span>%</span></strong><p>返赠积分</p></div></section>
    {fallback&&<label className={styles.fallback}>选择并复制<input aria-label="待复制的邀请信息" readOnly value={fallback} onFocus={event=>event.currentTarget.select()}/></label>}
    <div className={styles.stats}><div><span>已邀请好友</span><strong>{number(data.invited_count)}<small>人</small></strong></div><div><span>已充值好友</span><strong>{number(data.paid_count)}<small>人</small></strong></div><div><span>累计获得奖励</span><strong>{number(data.earned_credits)}<small>积分</small></strong></div></div>
    <section className={styles.friends} aria-label="好友邀请记录"><header><h2><UsersRound size={18}/>我的好友</h2><div className={styles.filters} aria-label="按充值状态筛选">{([['all','全部'],['unpaid','未充值'],['paid','已充值'],['refunded','已退款']] as const).map(([value,label])=><button type="button" key={value} aria-pressed={filter===value} onClick={()=>{setFilter(value);setOffset(0);}}>{label}</button>)}</div></header>
      {data.items.length===0?<div className={styles.empty}><UsersRound size={30}/><strong>{data.invited_count===0?"第一个好友，等你来邀请":"暂无这类好友记录"}</strong><p>{data.invited_count===0?"分享链接或邀请码，好友注册后会显示在这里。":"好友完成注册或充值后，状态会自动更新。"}</p></div>:<><div className={styles.tableHeading}><span>受邀好友</span><span>注册日期</span><span>充值状态</span><span>带来奖励</span></div><ul className={styles.rows}>{data.items.map(friend=><li key={friend.id}><strong>{friend.friend}</strong><time dateTime={friend.registered_at}>{day(friend.registered_at)}</time><span className={styles.status} data-status={friend.payment_status}>{labels[friend.payment_status]}</span><b>+{number(friend.earned_credits)}<small>积分</small></b></li>)}</ul><div className={styles.pagination}><span>共 {data.total} 位好友</span><div><button type="button" aria-label="上一页好友" disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-data.limit))}><ChevronLeft size={17}/></button><span>{Math.floor(offset/data.limit)+1}</span><button type="button" aria-label="下一页好友" disabled={offset+data.limit>=data.total} onClick={()=>setOffset(offset+data.limit)}><ChevronRight size={17}/></button></div></div></>}
    </section><div className={styles.rules}><span>赠送积分{data.validity_days}天有效</span><details><summary>邀请规则</summary><p>好友可通过邀请链接注册，或在注册时填写邀请码，关系绑定后不可更换。仅奖励直接邀请的好友为其个人账户购买积分包或会员，按每期实际到账积分的10%向下取整；未到账的后续会员周期不提前返赠，退款会同步调整奖励。奖励为创作积分，不兑换现金。好友联系方式已脱敏。</p></details></div>
  </div>;
}
