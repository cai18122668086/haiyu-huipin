"use client";

import { useCallback, useEffect, useState } from "react";
import { BellRing, Check, Pin, X } from "lucide-react";

import { usePublicAnnouncements } from "@/features/announcements/api/announcements";
import { useBodyScrollLock } from "@/lib/hooks/use-body-scroll-lock";

const NEVER_SHOW_KEY = "polaris.dismissed-pinned-announcements";
const SESSION_SEEN_KEY = "polaris.seen-pinned-announcements";

function readIds(key: string) {
  if (typeof window === "undefined") return [];
  try {
    const value = JSON.parse(window.localStorage.getItem(key) || "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function writeId(key: string, id: string) {
  try {
    const ids = new Set(readIds(key));
    ids.add(id);
    window.localStorage.setItem(key, JSON.stringify([...ids]));
  } catch {
    // Local storage can be unavailable in private browsing; the prompt still works for this render.
  }
}

export function AnnouncementPrompt() {
  const announcements = usePublicAnnouncements();
  const [dismissedIds, setDismissedIds] = useState<string[]>([]);
  const [neverShowAgain, setNeverShowAgain] = useState(false);
  const [promptConsumed, setPromptConsumed] = useState(false);
  const item = promptConsumed ? undefined : announcements.data?.items.find((announcement) => {
    if (!announcement.is_pinned || dismissedIds.includes(announcement.id)) return false;
    return !readIds(NEVER_SHOW_KEY).includes(announcement.id) && !readIds(SESSION_SEEN_KEY).includes(announcement.id);
  });

  const closePrompt = useCallback(() => {
    if (!item) return;
    writeId(SESSION_SEEN_KEY, item.id);
    if (neverShowAgain) writeId(NEVER_SHOW_KEY, item.id);
    setDismissedIds((current) => [...current, item.id]);
    setPromptConsumed(true);
    setNeverShowAgain(false);
  }, [item, neverShowAgain]);

  useBodyScrollLock(Boolean(item));

  useEffect(() => {
    if (!item) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closePrompt();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [closePrompt, item]);

  if (!item) return null;

  return (
    <div className="announcement-prompt__overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closePrompt(); }}>
      <section className="announcement-prompt" role="dialog" aria-modal="true" aria-labelledby="announcement-prompt-title">
        <header className="announcement-prompt__header">
          <div className="announcement-prompt__icon"><BellRing size={20} /></div>
          <div><span>重要公告</span><h2 id="announcement-prompt-title">{item.title}</h2></div>
          <button type="button" className="announcement-prompt__close" onClick={closePrompt} aria-label="关闭重要公告"><X size={17} /></button>
        </header>
        <div className="announcement-prompt__body">
          <div className="announcement-prompt__pin"><Pin size={13} />置顶公告</div>
          {item.summary && <p className="announcement-prompt__summary">{item.summary}</p>}
          {item.image_url && <img className="announcement-prompt__image" src={item.image_url} alt={item.image_alt || item.title} />}
          <div className="announcement-prompt__content">{item.content}</div>
        </div>
        <footer className="announcement-prompt__footer">
          <label className="announcement-prompt__check"><input type="checkbox" checked={neverShowAgain} onChange={(event) => setNeverShowAgain(event.target.checked)} /><span>以后不再弹出这条公告</span></label>
          <button type="button" className="button primary" onClick={closePrompt}><Check size={16} />确认</button>
        </footer>
      </section>
    </div>
  );
}
