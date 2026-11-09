export function welcomePromptEligible({ home, enabled, authenticated, status }: { home: boolean; enabled: boolean; authenticated: boolean | undefined; status?: string }) {
  return home && enabled && (authenticated === false || (authenticated === true && status === "available"));
}
export function beijingPromotionDay(now: Date) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}
