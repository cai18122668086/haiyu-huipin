const KEY = "huipin.invitation.v1";
const MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
const CODE = /^HY[A-F0-9]{10}$/;

export function parseInvitation(raw: string | null, now = Date.now()): string | undefined {
  if (!raw) return;
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || !("code" in value) || !("capturedAt" in value)) return;
    if (typeof value.code !== "string" || !CODE.test(value.code) || typeof value.capturedAt !== "number") return;
    const age = now - value.capturedAt;
    if (Number.isFinite(age) && age >= 0 && age < MAX_AGE_MS) return value.code;
  } catch { /* A malformed or expired attribution never blocks sign up. */ }
}

export function captureInvitation(search: string) {
  if (typeof window === "undefined") return;
  const code = new URLSearchParams(search).get("invite")?.trim().toUpperCase();
  if (!code || !CODE.test(code)) return;
  try {
    if (parseInvitation(window.localStorage.getItem(KEY))) return;
    window.localStorage.setItem(KEY, JSON.stringify({ code, capturedAt: Date.now() }));
  } catch { /* Storage can be unavailable in private or restricted browsers. */ }
}

export function readInvitationCode(): string | undefined {
  if (typeof window === "undefined") return;
  try { return parseInvitation(window.localStorage.getItem(KEY)); } catch { return; }
}

export function clearInvitationCode() {
  try { window.localStorage.removeItem(KEY); } catch { /* Registration already succeeded. */ }
}
