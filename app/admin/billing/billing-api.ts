import { isLocalDevelopmentAuthEnabled, LOCAL_DEV_AUTH_USER_ID } from "@/lib/auth/local-dev";

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  if (isLocalDevelopmentAuthEnabled()) headers.set("X-User-Id", LOCAL_DEV_AUTH_USER_ID);
  const response = await fetch(`/api/v1/admin/billing${path}`, { ...options, credentials: "include", cache: "no-store", headers });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message ?? "操作暂未完成，请稍后重试。");
  return result as T;
}

export const errorText = (error: unknown) => error instanceof Error ? error.message : "暂时无法加载";
export const money = (amount: number) => `¥${(amount / 100).toFixed(2)}`;

export async function exportOrders() {
  const headers = new Headers();
  if (isLocalDevelopmentAuthEnabled()) headers.set("X-User-Id", LOCAL_DEV_AUTH_USER_ID);
  const response = await fetch("/api/v1/admin/billing/orders-export", { credentials: "include", cache: "no-store", headers });
  if (!response.ok) {
    const result = await response.json();
    throw new Error(result.error?.message ?? "导出失败");
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url; link.download = "绘品支付订单.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
