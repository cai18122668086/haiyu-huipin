"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Search } from "lucide-react";
import styles from "./replica-select.module.css";

type Choice = readonly [value: string, label: string];
type Placement = { left: number; width: number; top?: number; bottom?: number; maxHeight: number };

export function ReplicaSelect({ value, onChange, options, label, disabled = false, layout = "list", searchable: allowSearch }: {
  value: string;
  onChange: (value: string) => void;
  options: readonly Choice[];
  label: string;
  disabled?: boolean;
  layout?: "list" | "grid";
  searchable?: boolean;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState<Placement>();
  const [query, setQuery] = useState("");
  const search = useRef<HTMLInputElement>(null);
  const searchable = allowSearch ?? (options.length >= 8 && layout === "list");
  const filteredOptions = options.filter(([key, text]) => `${key} ${text}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const selected = Math.max(0, options.findIndex(option => option[0] === value));
  const close = (restoreFocus = false) => {
    setPlacement(undefined);
    if (restoreFocus) trigger.current?.focus();
  };
  const open = () => {
    if (disabled || trigger.current?.matches(":disabled")) return;
    const rect = trigger.current?.getBoundingClientRect();
    if (!rect) return;
    const below = window.innerHeight - rect.bottom - 12;
    const above = rect.top - 12;
    const useAbove = below < 180 && above > below;
    setQuery("");
    setPlacement({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - rect.width - 8)),
      width: rect.width,
      ...(useAbove ? { bottom: window.innerHeight - rect.top + 6 } : { top: rect.bottom + 6 }),
      maxHeight: Math.min(280, Math.max(96, useAbove ? above : below)),
    });
    requestAnimationFrame(() => searchable ? search.current?.focus() : menu.current?.querySelectorAll<HTMLButtonElement>("[role=option]")[selected]?.focus());
  };
  useEffect(() => {
    if (!disabled) return;
    const frame = requestAnimationFrame(() => setPlacement(undefined));
    return () => cancelAnimationFrame(frame);
  }, [disabled]);
  useEffect(() => {
    if (!placement) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !menu.current?.contains(event.target) && !trigger.current?.contains(event.target)) setPlacement(undefined);
    };
    const viewportChange = (event: Event) => {
      if (event.target instanceof Node && menu.current?.contains(event.target)) return;
      setPlacement(undefined);
    };
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", viewportChange);
    window.addEventListener("scroll", viewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", viewportChange);
      window.removeEventListener("scroll", viewportChange, true);
    };
  }, [placement]);

  return <>
    <button ref={trigger} type="button" disabled={disabled} className={`${styles.trigger} ${placement ? styles.open : ""}`} aria-label={label} aria-haspopup="listbox" aria-expanded={!!placement} aria-controls={placement ? id : undefined} onClick={() => placement ? close() : open()} onKeyDown={event => {
      if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); open(); }
    }}>
      <span>{options[selected]?.[1]}</span><ChevronDown size={14} aria-hidden="true" />
    </button>
    {placement && createPortal(<div ref={menu} className={styles.menu} data-layout={layout} style={placement} onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); close(true); return; }
      if (event.key === "Tab") { close(true); return; }
      const buttons = [...(menu.current?.querySelectorAll<HTMLButtonElement>("[role=option]") ?? [])];
      const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
      if (event.target === search.current && !["ArrowDown", "ArrowUp"].includes(event.key)) return;
      const step = layout === "grid" ? 3 : 1;
      const navigationKey = ["Home", "End", "ArrowDown", "ArrowUp", ...(layout === "grid" ? ["ArrowLeft", "ArrowRight"] : [])].includes(event.key);
      if (!buttons.length && navigationKey) { event.preventDefault(); return; }
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : event.key === "ArrowDown" ? current < 0 ? 0 : (current + step) % buttons.length : event.key === "ArrowUp" ? current < 0 ? buttons.length - 1 : (current - step + buttons.length) % buttons.length : layout === "grid" && event.key === "ArrowRight" ? (current + 1) % buttons.length : layout === "grid" && event.key === "ArrowLeft" ? (current - 1 + buttons.length) % buttons.length : -1;
      if (next >= 0) { event.preventDefault(); buttons[next]?.focus(); }
    }}>
      {searchable && <div className={styles.search}><Search size={14} aria-hidden="true" /><input ref={search} value={query} onChange={event => setQuery(event.target.value)} placeholder={`搜索${label}`} aria-label={`搜索${label}`} /></div>}
      <div id={id} role="listbox" aria-label={label} className={styles.options}>
        {filteredOptions.map(([key, text]) => <button key={key} type="button" role="option" aria-selected={value === key} className={value === key ? styles.selected : ""} onClick={() => { if (disabled || trigger.current?.matches(":disabled")) return; onChange(key); close(true); }}><span>{text}</span>{value === key && <Check size={14} aria-hidden="true" />}</button>)}
      </div>
      {!filteredOptions.length && <p className={styles.empty} role="status">没有匹配的选项，试试其他关键词</p>}
    </div>, document.body)}
  </>;
}
