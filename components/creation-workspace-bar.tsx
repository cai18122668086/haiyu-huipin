"use client";

import Link from "next/link";
import { ArrowLeft, Clock3, FolderOpen, Lightbulb } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ALL_TOOLS_MENU } from "@/lib/navigation-registry";
import { FEATURE_REGISTRY } from "@/lib/feature-registry";
import { PRIMARY_CREATION_ENTRIES } from "@/lib/creation-entrypoints";
import { NavigationMegaMenu } from "./navigation-mega-menu";

export function CreationWorkspaceBar({ featureId }: { featureId: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLElement>(null);
  const feature = FEATURE_REGISTRY.find(item => item.id === featureId);
  const entry = PRIMARY_CREATION_ENTRIES.find(item => item.id === featureId);
  const label = entry?.label ?? feature?.title ?? "创作工具";
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { setOpen(false); root.current?.querySelector<HTMLButtonElement>(".workspace-tool-picker")?.focus(); } };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onPointer); document.removeEventListener("keydown", onKey); };
  }, [open]);
  return <nav className="creation-workspace-bar" aria-label="创作工具切换" ref={root}>
    <div className="creation-workspace-bar__left"><Link href="/#start" className="creation-back" aria-label="返回创作首页"><ArrowLeft size={20} /></Link><NavigationMegaMenu definition={{ ...ALL_TOOLS_MENU, label }} activeFeatureId={featureId} active open={open} onClose={() => setOpen(false)} onToggle={() => setOpen(value => !value)} idPrefix="workspace-tool-menu" triggerAsButton /></div>
    <div className="creation-workspace-bar__right"><Link href="/inspiration" aria-label="找灵感"><Lightbulb size={15} /><span>找灵感</span></Link><Link href="/assets" aria-label="我的素材"><FolderOpen size={15} /><span>我的素材</span></Link><Link href="/jobs" aria-label="历史记录"><Clock3 size={15} /><span>历史记录</span></Link></div>
  </nav>;
}
