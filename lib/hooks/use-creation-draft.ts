"use client";

import { useCallback, useRef, useSyncExternalStore } from "react";

const EVENT = "huipin-creation-draft";
const cache = new Map<string, { raw: string | null; value: unknown }>();
const lifetime = 30 * 24 * 60 * 60 * 1000;
const prefix = (workspace: string, tool: string) => `huipin-draft:${workspace}:${tool}:`;

export function clearCreationDraft(workspace: string, tool: string) {
  const start = prefix(workspace, tool);
  try {
    Object.keys(localStorage).filter(key => key.startsWith(start)).forEach(key => {
      localStorage.removeItem(key);
      cache.delete(key);
    });
  } catch { /* The current form remains usable when browser storage is blocked. */ }
  window.dispatchEvent(new Event(EVENT));
}

/** Store only form values and workspace-scoped asset IDs, never File objects. */
export function useDraftState<T>(workspace: string | undefined, tool: string, field: string, initial: T) {
  const fallback = useRef(initial);
  const key = workspace ? prefix(workspace, tool) + field : undefined;
  const read = useCallback((): T => {
    if (!key) return fallback.current;
    try {
      const raw = localStorage.getItem(key);
      const old = cache.get(key);
      if (old?.raw === raw) return old.value as T;
      const saved = raw ? JSON.parse(raw) : undefined;
      const value = saved && Date.now() - saved.updatedAt < lifetime ? saved.value as T : fallback.current;
      cache.set(key, { raw, value });
      return value;
    } catch { return (cache.get(key)?.value as T) ?? fallback.current; }
  }, [key]);
  const subscribe = useCallback((notify: () => void) => {
    window.addEventListener(EVENT, notify);
    window.addEventListener("storage", notify);
    return () => { window.removeEventListener(EVENT, notify); window.removeEventListener("storage", notify); };
  }, []);
  const value = useSyncExternalStore(subscribe, read, () => fallback.current);
  const setValue = useCallback((next: T | ((current: T) => T)) => {
    if (!key) return;
    const value = typeof next === "function" ? (next as (current: T) => T)(read()) : next;
    const raw = JSON.stringify({ updatedAt: Date.now(), value }, (field, item) => field === "previewUrl" && typeof item === "string" && item.startsWith("blob:") ? "" : item);
    cache.set(key, { raw, value });
    try { localStorage.setItem(key, raw); } catch {
      let previous: string | null = null;
      try { previous = localStorage.getItem(key); } catch { /* Storage is blocked. */ }
      cache.set(key, { raw: previous, value });
    }
    window.dispatchEvent(new Event(EVENT));
  }, [key, read]);
  return [value, setValue] as const;
}
