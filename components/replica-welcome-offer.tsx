"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, Gift, Sparkles } from "lucide-react";
import { requestLogin } from "@/lib/auth/session-events";
import styles from "./replica-welcome-offer.module.css";

export const REPLICA_OFFER_PREVIEW_QUERY = "replica_offer_preview";

export function ReplicaWelcomeOffer({ compact = false }: { compact?: boolean }) {
  return <section className={compact ? styles.compact : styles.offer} aria-label="一键复刻新客体验">
    <div className={styles.copy}>
      <span className={styles.eyebrow}><Gift size={15} aria-hidden="true" /> 新客见面礼</span>
      <h2>注册就送<span>一整套</span></h2>
      <p className={styles.headline}>全网最牛一键复刻</p>
      {!compact && <p className={styles.description}>上传自己的商品实拍，参考同行风格，快速做出自己的电商套图。</p>}
      <p className={styles.terms}>一次快速复刻 · 最多 10 张参考图 · 拆屏不额外收费</p>
    </div>
    {!compact && <div className={styles.action}>
      <span className={styles.coupon}><Sparkles size={20} aria-hidden="true" /><strong>1<small>套</small></strong><span>快速复刻体验</span></span>
      <button type="button" onClick={() => requestLogin(`/style-replica?${REPLICA_OFFER_PREVIEW_QUERY}=1`)}>免费领取，立即复刻 <ArrowUpRight size={18} aria-hidden="true" /></button>
    </div>}
  </section>;
}

/** Draft only: production never advertises an entitlement that has not shipped. */
export function ReplicaWelcomeOfferPreview() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") {
      setVisible(new URLSearchParams(window.location.search).get(REPLICA_OFFER_PREVIEW_QUERY) === "1");
    }
  }, []);
  if (!visible) return null;
  return <div className={styles.preview}>
    <p className={styles.previewLabel}>活动界面预览 · 赠送权益尚未开放领取</p>
    <ReplicaWelcomeOffer />
  </div>;
}
