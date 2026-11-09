"use client";
import { useEffect, useState } from "react";
import Link from "@/components/seo-link";
import { ReplicaWelcomeModal, ReplicaWelcomeOffer } from "./replica-welcome-offer";
import { NewcomerOfferModal, NewcomerFloating } from "./newcomer-offer-controller";
import { BrandMark } from "./brand-mark";
import { DisplayImage } from "./display-image";
import styles from "./promotion-preview.module.css";
export default function PromotionPreview() {
  const [modal,setModal] = useState<"free"|"paid"|null>(null);
  const [floating,setFloating] = useState(true);
  const [clock] = useState(()=>Date.now());
  const [now,setNow] = useState(clock);
  useEffect(()=>{const id=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(id);},[]);
  const left=Math.max(0,5*3600+26*60+18-Math.floor((now-clock)/1000));
  const parts=[Math.floor(left/3600),Math.floor(left%3600/60),left%60].map(x=>String(x).padStart(2,"0"));
  const offer={eligible:true,trial_viewed:true,reminded_today:false,should_show:true,server_now:new Date(clock).toISOString(),ready_at:new Date(clock).toISOString(),window_ends_at:new Date(clock+(5*3600+26*60+18)*1000).toISOString(),amount_fen:990,regular_amount_fen:990,savings_fen:0,credits:240,fast_images:30,validity_days:30};
  return <main className={styles.page}>
    <header className={styles.nav}><BrandMark/><span>红金活动预览</span><Link href="/">查看实际首页</Link></header>
    <div className={styles.banner}><ReplicaWelcomeOffer/></div>
    <section className={styles.content}>
      <p className={styles.note}>本地预览 · 测试账号 · 不会产生真实付款或生图</p>
      <h1>新的福利，从这里开始。</h1><p>查看免费体验、首单优惠及套餐。页面复用实际组件。</p>
      <div className={styles.controls}><button onClick={()=>setModal("free")}>查看免费弹窗</button><button onClick={()=>setModal("paid")}>查看9.9元弹窗</button><button onClick={()=>setFloating(x=>!x)}>{floating?"隐藏":"显示"}悬浮入口</button><Link href="/account#membership">查看套餐与单价</Link><Link href="/account#rewards">查看签到规则</Link></div>
      <h2 id="preview-cases">真实复刻案例</h2><div className={styles.grid}>{["01","02","03","04","05","06"].map(id=><DisplayImage key={id} src={`/images/cases/olay-20261030/replica-${id}.png`} alt={`真实复刻案例${id}`} width={1254} height={1254}/>)}</div>
    </section>
    {modal==="free"&&<ReplicaWelcomeModal onClose={()=>setModal(null)} onStart={()=>{window.location.href="/style-replica?welcome=1";}} onExamples={()=>{setModal(null);document.getElementById("preview-cases")?.scrollIntoView({behavior:"smooth"});}}/>}
    {modal==="paid"&&<NewcomerOfferModal offer={offer} receivedAt={clock} now={now} onClose={()=>setModal(null)}/>}
    {floating&&!modal&&<NewcomerFloating amount={9.9} parts={parts} onOpen={()=>setModal("paid")} onClose={()=>setFloating(false)}/>}
  </main>;
}
