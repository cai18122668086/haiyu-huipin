import { parseAuthEvidence, type AuthEvidence } from "./diagnostic-evidence";

export type AuthPhase = "session" | "email_verification";
export type AuthFailureReason = "timeout" | "offline" | "network" | "server" | "unexpected";
export type AuthDiagnostic = {
  id: string;
  occurred_at: string;
  phase: AuthPhase;
  reason: AuthFailureReason;
  path: string;
  elapsed_ms: number;
  http_status: number | null;
  request_ids: string[];
  evidence?: AuthEvidence;
};

export function authFailureReason(error: unknown, online: boolean, status?: number): AuthFailureReason {
  if (!online) return "offline";
  if (status && status >= 500) return "server";
  if (error instanceof Error) {
    if (["TimeoutError", "AbortError"].includes(error.name) || error.message === "AUTH_CHECK_TIMEOUT") return "timeout";
    if (error instanceof TypeError) return "network";
  }
  return "unexpected";
}

export function authFailureCanRetry(reason: AuthFailureReason, status?: number): boolean {
  return !(status && status >= 400 && status < 500) && ["timeout", "network", "server"].includes(reason);
}

// Only named fields are accepted; URLs, exception messages and credentials
// never become part of the diagnostic sent to the server.
export function parseAuthDiagnostic(value: unknown): AuthDiagnostic | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Record<string, unknown>;
  if (typeof data.id !== "string" || !/^auth_[0-9a-f-]{36}$/i.test(data.id)) return null;
  if (typeof data.occurred_at !== "string" || !Number.isFinite(Date.parse(data.occurred_at))) return null;
  if (!["session", "email_verification"].includes(String(data.phase))) return null;
  if (!["timeout", "offline", "network", "server", "unexpected"].includes(String(data.reason))) return null;
  if (typeof data.path !== "string" || !/^\/(?:[a-zA-Z0-9_/-]{0,120})$/.test(data.path)) return null;
  if (!Number.isInteger(data.elapsed_ms) || Number(data.elapsed_ms) < 0 || Number(data.elapsed_ms) > 120_000) return null;
  if (data.http_status !== null && (!Number.isInteger(data.http_status) || Number(data.http_status) < 400 || Number(data.http_status) > 599)) return null;
  if (!Array.isArray(data.request_ids) || data.request_ids.length > 6 || !data.request_ids.every((id) => typeof id === "string" && /^[a-zA-Z0-9_-]{1,80}$/.test(id))) return null;
  const evidence = data.evidence === undefined ? undefined : parseAuthEvidence(data.evidence);
  if (evidence === null) return null;
  return {
    id: data.id,
    occurred_at: new Date(data.occurred_at).toISOString(),
    phase: data.phase as AuthPhase,
    reason: data.reason as AuthFailureReason,
    path: data.path,
    elapsed_ms: Number(data.elapsed_ms),
    http_status: data.http_status === null ? null : Number(data.http_status),
    request_ids: [...data.request_ids],
    ...(evidence ? { evidence } : {}),
  };
}
