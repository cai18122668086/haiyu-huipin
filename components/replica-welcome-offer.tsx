"use client";

import { useRouter } from "next/navigation";
import { useAuthSession } from "@/features/auth/api/session";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { usePublicReplicaWelcome, useReplicaWelcome } from "@/features/billing/api/replica-welcome";
import { ArrowUpRight } from "lucide-react";
import { requestLogin } from "@/lib/auth/session-events";
import styles from "./replica-welcome-offer.module.css";

export function ReplicaWelcomeOffer({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const auth = useAuthSession();
  const { workspace } = useWorkspace();
  const welcome = useReplicaWelcome(workspace?.id);
  const target = welcome.data?.session_id
    ? `/style-replica?sessionId=${encodeURIComponent(welcome.data.session_id)}`
    : "/style-replica?welcome=1";
  return <section className={compact ? styles.compact : styles.offer} aria-label="一键复刻新客体验">
    <div className={styles.copy}>
      <h2>注册就送<span>整套商品图</span></h2>
      <p className={styles.headline}>2000元级美工效果</p>
    </div>
    {!compact && <div className={styles.action}>
      <button type="button" onClick={() => auth.data ? router.push(target) : requestLogin(target)}><span>{welcome.data?.session_id ? "继续免费复刻" : "立即领取"}</span><ArrowUpRight size={19} aria-hidden="true" /></button>
    </div>}
  </section>;
}

export function ReplicaWelcomeBanner() {
  const promotion = usePublicReplicaWelcome();
  const { workspace } = useWorkspace();
  const welcome = useReplicaWelcome(workspace?.id);
  const eligible = !workspace || ["available", "in_progress", "recoverable"].includes(welcome.data?.status ?? "");
  if (!(promotion.data?.enabled && eligible)) return null;
  return <div className={styles.preview}><ReplicaWelcomeOffer /></div>;
}
