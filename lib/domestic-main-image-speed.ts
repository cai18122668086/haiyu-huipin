import { useCallback, useSyncExternalStore } from "react";

export type MainImageSpeed = "standard" | "fast";

export function mainImageSpeed(value: unknown): MainImageSpeed {
  return value === "fast" ? "fast" : "standard";
}

// Omit the standard default so legacy request identities remain unchanged.
export function mainImageSpeedSettings(speed: MainImageSpeed): { main_image_speed?: "fast" } {
  return speed === "fast" ? { main_image_speed: "fast" } : {};
}

const SPEED_DRAFT_EVENT = "huipin-main-image-speed-change";
const memoryDrafts = new Map<string, MainImageSpeed>();

function subscribe(listener: () => void) {
  window.addEventListener("storage", listener);
  window.addEventListener(SPEED_DRAFT_EVENT, listener);
  return () => {
    window.removeEventListener("storage", listener);
    window.removeEventListener(SPEED_DRAFT_EVENT, listener);
  };
}

export function useMainImageSpeedDraft(workspaceId?: string): readonly [MainImageSpeed, (speed: MainImageSpeed) => void] {
  const key = workspaceId ? `huipin-main-image-speed:${workspaceId}` : undefined;
  const snapshot = useCallback(() => {
    if (!key) return "standard";
    try { return mainImageSpeed(localStorage.getItem(key)); } catch { return memoryDrafts.get(key) ?? "standard"; }
  }, [key]);
  const speed = useSyncExternalStore(subscribe, snapshot, () => "standard" as MainImageSpeed);
  const setSpeed = useCallback((value: MainImageSpeed) => {
    if (!key) return;
    memoryDrafts.set(key, value);
    try { localStorage.setItem(key, value); } catch { /* Keep an in-memory draft when storage is unavailable. */ }
    window.dispatchEvent(new Event(SPEED_DRAFT_EVENT));
  }, [key]);
  return [speed, setSpeed] as const;
}
