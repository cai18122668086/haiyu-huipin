import { useCallback, useSyncExternalStore } from "react";

export type MainImageSpeed = "standard" | "fast" | "native_fast";
export type MainImageDraftSpeed = Exclude<MainImageSpeed, "fast">;

export const MAIN_IMAGE_SPEED_OPTIONS = [
  ["standard", "原版", "按现有流程规划、生成与审核"],
  ["native_fast", "快速B·原生精简", "保留原生风格与六图方案，精简生成流程"],
] as const;

export function mainImageSpeed(value: unknown): MainImageSpeed {
  return value === "fast" || value === "native_fast" ? value : "standard";
}

// Retired option A migrates only an unsent local draft, never a saved job.
export function mainImageDraftSpeed(value: unknown): MainImageDraftSpeed {
  return value === "fast" || value === "native_fast" ? "native_fast" : "standard";
}

export function mainImageHistoryLabel(speed: MainImageSpeed): string {
  if (speed === "fast") return "快速A·历史版本";
  if (speed === "native_fast") return "快速B·原生精简";
  return "原版";
}

// Keep each submitted point as one authored line, including its punctuation.
export function mainImageSellingPoints(value: string): string[] {
  return value.split(/\r?\n/).map(item => item.trim()).filter(Boolean).slice(0, 24);
}

// Omit the standard default so legacy request identities remain unchanged.
export function mainImageSpeedSettings(speed: MainImageSpeed): { main_image_speed?: Exclude<MainImageSpeed, "standard"> } {
  return speed === "standard" ? {} : { main_image_speed: speed };
}

// Rendering follows the persisted plan format, independently of execution speed.
// Native-fast keeps Aurora's original object identity and full page contracts.
export function mainImageUsesCompactPlan(payload?: Record<string, unknown>): boolean {
  return typeof payload?.product_identity_lock === "string"
    && Array.isArray(payload.pages)
    && payload.pages.some((page: unknown) => Boolean(page && typeof page === "object"
      && "visual_plan" in page && typeof page.visual_plan === "string"));
}

const SPEED_DRAFT_EVENT = "huipin-main-image-speed-change";
const memoryDrafts = new Map<string, MainImageDraftSpeed>();

function subscribe(listener: () => void) {
  window.addEventListener("storage", listener);
  window.addEventListener(SPEED_DRAFT_EVENT, listener);
  return () => {
    window.removeEventListener("storage", listener);
    window.removeEventListener(SPEED_DRAFT_EVENT, listener);
  };
}

export function useMainImageSpeedDraft(workspaceId?: string): readonly [MainImageDraftSpeed, (speed: MainImageDraftSpeed) => void] {
  const key = workspaceId ? `huipin-main-image-speed:${workspaceId}` : undefined;
  const snapshot = useCallback(() => {
    if (!key) return "standard";
    try { return mainImageDraftSpeed(localStorage.getItem(key)); } catch { return memoryDrafts.get(key) ?? "standard"; }
  }, [key]);
  const speed = useSyncExternalStore(subscribe, snapshot, () => "standard" as MainImageDraftSpeed);
  const setSpeed = useCallback((value: MainImageDraftSpeed) => {
    if (!key) return;
    memoryDrafts.set(key, value);
    try { localStorage.setItem(key, value); } catch { /* Keep an in-memory draft when storage is unavailable. */ }
    window.dispatchEvent(new Event(SPEED_DRAFT_EVENT));
  }, [key]);
  return [speed, setSpeed] as const;
}
