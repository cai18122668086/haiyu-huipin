type ReminderState = {
  eligible: boolean;
  trial_viewed: boolean;
  reminded_today: boolean;
  ready_at: string | null;
  server_now: string;
};

export function newcomerReminderReady(data: ReminderState | undefined, elapsedMs: number) {
  if (!data?.eligible || !data.trial_viewed || data.reminded_today || !data.ready_at) return false;
  return Date.parse(data.server_now) + Math.max(0, elapsedMs) >= Date.parse(data.ready_at);
}

export function newcomerDialogBlocks(element: Element) {
  // Task details contain the actual result and can remain open. Full-screen
  // previews, login, payments and confirmations must finish before an upsell.
  if (element.matches('[data-newcomer-result-detail="true"]') || element.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
  if (typeof window !== "undefined") {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    if (style.display === "none" || style.visibility !== "visible" || Number(style.opacity) === 0 || rect.width === 0 || rect.height === 0 || rect.right <= 0 || rect.bottom <= 0 || rect.left >= window.innerWidth || rect.top >= window.innerHeight) return false;
  }
  return true;
}

export function newcomerResultSource(id: string) {
  return id.startsWith("rep_") ? { session_id: id } : { job_id: id };
}
