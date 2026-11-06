"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { requestLogin } from "@/lib/auth/session-events";
import styles from "./replica-welcome-offer.module.css";

export const REPLICA_OFFER_PREVIEW_QUERY = "replica_offer_preview";

export function ReplicaWelcomeOffer({ compact = false }: { compact?: boolean }) {
  return <section className={compact ? styles.compact : styles.offer} aria-label="一键复刻新客体验">
    <div className={styles.copy}>
      <h2>注册就送<span>一整套</span></h2>
      <p className={styles.headline}>全网最牛一键复刻</p>
    </div>
    {!compact && <div className={styles.action}>
      <button type="button" onClick={() => requestLogin(`/style-replica?${REPLICA_OFFER_PREVIEW_QUERY}=1`)}><span>免费领取<span className={styles.buttonExtra}>一套</span></span><ArrowUpRight size={19} aria-hidden="true" /></button>
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
  return <div className={styles.preview}><ReplicaWelcomeOffer /></div>;
}
