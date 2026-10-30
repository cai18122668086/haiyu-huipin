export type DetailSpeed = "standard" | "fast";

export const DETAIL_SPEED_OPTIONS = [
  { value: "fast", title: "快速版（约5分钟 · 8积分/张）" },
  { value: "standard", title: "精致版（约40分钟 · 15积分/张）" },
] as const;

export function detailSpeedSettings(region: "domestic" | "international", speed: DetailSpeed) {
  // Keep the original request and its idempotency identity unchanged.
  return region === "domestic" && speed === "fast" ? { detail_speed: "fast" as const } : {};
}

export function savedDetailSpeed(configuration?: { domestic_detail_pipeline?: unknown; settings?: unknown }): DetailSpeed {
  return configuration?.domestic_detail_pipeline === "xinghe-domestic-detail-v4.0-fast" ? "fast" : "standard";
}
