"use client";

import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useAuthSession } from "@/features/auth/api/session";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { usePublicReplicaWelcome, useReplicaWelcome } from "@/features/billing/api/replica-welcome";
import { ArrowRight, ArrowUpRight, X } from "lucide-react";
import { requestLogin } from "@/lib/auth/session-events";
import { useBodyScrollLock } from "@/lib/hooks/use-body-scroll-lock";
import { newcomerDialogBlocks } from "@/lib/newcomer-offer-policy";
import { welcomePromptEligible, beijingPromotionDay } from "@/lib/welcome-prompt-policy";
import { DisplayImage } from "./display-image";
import styles from "./replica-welcome-offer.module.css";

export function ReplicaWelcomeOffer({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const auth = useAuthSession();
  const { workspace } = useWorkspace();
  const welcome = useReplicaWelcome(workspace?.id);
  const target = welcome.data?.session_id ? `/style-replica?sessionId=${encodeURIComponent(welcome.data.session_id)}` : "/style-replica?welcome=1";
  return <section className={compact ? styles.compact : styles.offer} aria-label="一键复刻新客体验">
    <div className={styles.copy}><h2>首套商品图，<span>免费做</span></h2><p className={styles.headline}>新用户专享，先看成套效果</p></div>
    {!compact && <div className={styles.action}><button type="button" onClick={() => auth.data ? router.push(target) : requestLogin(target)}><span>{welcome.data?.session_id ? "继续免费复刻" : "立即免费试做"}</span><ArrowUpRight size={19} aria-hidden="true" /></button></div>}
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

export function ReplicaWelcomeModal({ onClose, onStart, onExamples }: { onClose: () => void; onStart: () => void; onExamples: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useBodyScrollLock(true);
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);
  return <dialog ref={dialog} className={styles.dialog} aria-labelledby="free-welcome-title" onCancel={event => { event.preventDefault(); onClose(); }}>
    <header className={styles.modalTop}>
      <button type="button" className={styles.close} aria-label="关闭免费体验" onClick={onClose}><X size={23}/></button>
      <h2 id="free-welcome-title">首套商品图，免费做</h2><p>用自己的商品，试出整套效果</p>
    </header>
    <div className={styles.modalBody}>
      <div className={styles.examples} aria-label="身体乳真实复刻案例，商品原图与三张成图">
        <figure className={styles.original}><DisplayImage src="/images/cases/olay-20261030/product-01.webp" alt="身体乳商品原图" width={720} height={960}/><figcaption>商品原图</figcaption></figure>
        <ArrowRight className={styles.exampleArrow} size={22}/>
        <div className={styles.results}>{["01", "02", "04"].map(id => <DisplayImage key={id} src={`/images/cases/olay-20261030/replica-${id}.png`} alt={`身体乳真实复刻成图${id}`} width={1254} height={1254} sizes="(max-width: 640px) 22vw, 150px"/>)}<span>真实案例 · 部分成图</span></div>
      </div>
      <button type="button" className={styles.primary} onClick={onStart}>立即免费试做<ArrowUpRight size={21}/></button>
      <button type="button" className={styles.secondary} onClick={onExamples}>先看看效果<ArrowRight size={15}/></button>
      <footer className={styles.terms}>新用户专享 · 免费快速复刻一次<details><summary>体验规则</summary><p>新注册且已验证手机号的个人账户，同一手机号限一次。注册后7天内开始，最多10张参考图。其他生图功能按实际积分计费。</p></details></footer>
    </div>
  </dialog>;
}

export function ReplicaWelcomePrompt() {
  const pathname = usePathname();
  const search = useSearchParams();
  const router = useRouter();
  const auth = useAuthSession();
  const { workspace } = useWorkspace();
  const promotion = usePublicReplicaWelcome();
  const welcome = useReplicaWelcome(workspace?.id);
  const [open, setOpen] = useState(false);
  const shown = useRef(false);
  const status = welcome.data?.enabled ? welcome.data.status : undefined;
  const eligible = welcomePromptEligible({ home: pathname === "/" && !search.has("preview"), enabled: promotion.data?.enabled === true, authenticated: auth.data, status });
  useEffect(() => {
    if (!eligible || shown.current) return;
    let lastInteraction = Date.now();
    const defer = () => { lastInteraction = Date.now(); };
    const prefix = "huipin-free-welcome-v1";
    const day = beijingPromotionDay(new Date());
    const keys = [prefix, ...(workspace?.id ? [`${prefix}:${workspace.id}`] : [])];
    const dismissed = () => { try { return keys.some(key => localStorage.getItem(key) === day); } catch { return false; } };
    if (dismissed()) return;
    const timer = window.setInterval(() => {
      if (shown.current || dismissed() || document.visibilityState !== "visible" || Date.now() - lastInteraction < 5000) return;
      if (document.activeElement?.matches('input,textarea,select,[contenteditable="true"]')) return;
      if (Array.from(document.querySelectorAll('dialog[open], [role="dialog"], [aria-modal="true"]')).some(newcomerDialogBlocks)) { defer(); return; }
      shown.current = true;
      try { keys.forEach(key => localStorage.setItem(key, day)); } catch { /* Session-level suppression still works. */ }
      setOpen(true);
    }, 500);
    window.addEventListener("pointerdown", defer);
    window.addEventListener("keydown", defer);
    window.addEventListener("scroll", defer, true);
    document.addEventListener("visibilitychange", defer);
    return () => { clearInterval(timer); window.removeEventListener("pointerdown", defer); window.removeEventListener("keydown", defer); window.removeEventListener("scroll", defer, true); document.removeEventListener("visibilitychange", defer); };
  }, [eligible, workspace?.id]);
  if (!open || !eligible) return null;
  return <ReplicaWelcomeModal onClose={() => setOpen(false)} onStart={() => { setOpen(false); const target = "/style-replica?welcome=1"; if (auth.data) router.push(target); else requestLogin(target); }} onExamples={() => { setOpen(false); document.getElementById("start")?.scrollIntoView({behavior:"smooth"}); }} />;
}
