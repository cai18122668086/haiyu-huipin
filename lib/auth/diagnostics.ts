import { authFailureReason, type AuthDiagnostic, type AuthPhase } from "./diagnostic-payload";
import { authFailureLocation, type AuthCheckTrace, type AuthErrorName, type AuthRequestTrace } from "./diagnostic-evidence";

export type AuthDiagnosticRun = {
  id: string;
  started: number;
  checks: AuthCheckTrace[];
  requests: AuthRequestTrace[];
  requestIds: string[];
  closed: boolean;
};
export type AuthTraceContext = { run: AuthDiagnosticRun; check: AuthCheckTrace };
export type AuthRequestObservation = { context: AuthTraceContext; trace: AuthRequestTrace; dispose?: () => void };
let defaultRun: AuthDiagnosticRun | undefined;
let diagnosticTransport: typeof fetch | undefined;
const contexts = new WeakSet<object>();
const activeChecks = new Set<AuthTraceContext>();
const requests = new Map<string, AuthRequestObservation>();
const validId = (id: unknown): id is string => typeof id === "string" && /^[a-zA-Z0-9_-]{1,80}$/.test(id);

export const authTraceTime = (run: AuthDiagnosticRun) => Math.min(120_000, Math.max(0, Math.round(performance.now() - run.started)));
export function authErrorName(error: unknown): AuthErrorName {
  return error instanceof Error && ["AbortError", "TimeoutError", "TypeError", "SyntaxError", "Error"].includes(error.name) ? error.name as AuthErrorName : "Other";
}
export function beginAuthDiagnostics(): AuthDiagnosticRun {
  const run: AuthDiagnosticRun = { id: `auth_${crypto.randomUUID()}`, started: performance.now(), checks: [], requests: [], requestIds: [], closed: false };
  defaultRun = run;
  return run;
}
export function finishAuthDiagnostics(run: AuthDiagnosticRun) {
  run.closed = true;
  for (const context of activeChecks) if (context.run === run) activeChecks.delete(context);
  for (const [id, request] of requests) {
    if (request.context.run === run) { request.dispose?.(); requests.delete(id); }
  }
  if (defaultRun === run) defaultRun = undefined;
}

export async function observeAuthCheck<T>(run: AuthDiagnosticRun, attempt: number, phase: AuthPhase, operation: (context: AuthTraceContext) => Promise<T>): Promise<T> {
  const check: AuthCheckTrace = { attempt, phase, started_ms: authTraceTime(run), outcome: "pending" };
  const context = { run, check };
  contexts.add(context);
  activeChecks.add(context);
  run.checks.push(check);
  run.checks = run.checks.slice(-4);
  try {
    const result = await operation(context);
    check.outcome = "resolved";
    return result;
  } catch (error) {
    check.outcome = "rejected";
    check.error_name = authErrorName(error);
    throw error;
  } finally {
    check.finished_ms = authTraceTime(run);
    activeChecks.delete(context);
    for (const request of requests.values()) if (request.context === context) request.dispose?.();
  }
}

export function rememberAuthRequest(id: string, run = defaultRun) {
  if (run && !run.closed && validId(id) && !run.requestIds.includes(id)) run.requestIds = [...run.requestIds, id].slice(-6);
}
export function prepareAuthRequest(input: { id: string; url: string; userContext?: unknown; signal?: AbortSignal | null }) {
  const candidate = input.userContext && typeof input.userContext === "object" && "polarisAuthTrace" in input.userContext ? input.userContext.polarisAuthTrace : undefined;
  // Some refresh paths in the pinned SDK omit userContext. Associate those only
  // when exactly one check is active. Timed-out, still-running checks stay in
  // the set, so ambiguous late work is not attributed to a newer attempt.
  const context = candidate && typeof candidate === "object" && contexts.has(candidate) ? candidate as AuthTraceContext : candidate === undefined && activeChecks.size === 1 ? [...activeChecks][0] : undefined;
  if (!context || context.run.closed || !validId(input.id)) return;
  const path = new URL(input.url, "http://localhost").pathname;
  if (!path.startsWith("/api/auth/")) return;
  const trace: AuthRequestTrace = {
    id: input.id, attempt: context.check.attempt, phase: context.check.phase,
    endpoint: path === "/api/auth/user/email/verify" ? "email_status" : path === "/api/auth/session/refresh" ? "session_refresh" : "other_auth",
    prepared_ms: authTraceTime(context.run),
  };
  const observation: AuthRequestObservation = { context, trace };
  if (input.signal) {
    const signal = input.signal;
    const aborted = () => { if (!context.run.closed) { trace.aborted_ms = authTraceTime(context.run); trace.error_name = authErrorName(signal.reason); } };
    signal.addEventListener("abort", aborted, { once: true });
    observation.dispose = () => signal.removeEventListener("abort", aborted);
    if (signal.aborted) aborted();
  }
  requests.get(input.id)?.dispose?.();
  requests.set(input.id, observation);
  context.run.requests.push(trace);
  context.run.requests = context.run.requests.slice(-6);
  rememberAuthRequest(input.id, context.run);
  while (requests.size > 24) {
    const oldest = requests.keys().next().value;
    if (oldest === undefined) break;
    requests.get(oldest)?.dispose?.(); requests.delete(oldest);
  }
}
export function authRequestObservation(input: RequestInfo | URL, init?: RequestInit) {
  const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
  const id = headers.get("X-Request-Id");
  return id ? requests.get(id) : undefined;
}

export async function observeAuthResponse(input: { fetchResponse: Response; requestInit?: RequestInit }) {
  const id = input.fetchResponse.headers.get("X-Request-Id");
  const sentId = new Headers(input.requestInit?.headers).get("X-Request-Id");
  const request = (sentId && requests.get(sentId)) || (id && requests.get(id));
  if (request && !request.context.run.closed) {
    request.trace.sdk_response_ms = authTraceTime(request.context.run);
    request.trace.http_status = input.fetchResponse.status;
    if (validId(id)) { request.trace.server_id = id; rememberAuthRequest(id, request.context.run); }
  }
}

export function createAuthDiagnostic(error: unknown, phase: AuthPhase, started: number, run = defaultRun): AuthDiagnostic {
  const response = error instanceof Response ? error : error && typeof error === "object" && "response" in error ? error.response : undefined;
  const check = run?.checks.filter((check) => check.phase === phase).at(-1);
  const request = run?.requests.filter((request) => request.attempt === check?.attempt && request.phase === phase).at(-1);
  if (response instanceof Response) {
    const id = response.headers.get("X-Request-Id");
    if (id) rememberAuthRequest(id, run);
  }
  // Keep the legacy error-only status for retry policy compatibility. V2 records
  // successful HTTP status codes separately, on their own request/attempt.
  const status = response instanceof Response && response.status >= 400 ? response.status : request?.http_status !== undefined && request.http_status >= 400 ? request.http_status : undefined;
  const reason = authFailureReason(error, navigator.onLine, status);
  if (run && check && reason === "timeout") check.timed_out_ms = authTraceTime(run);
  const path = window.location.pathname;
  return {
    id: run?.id ?? `auth_${crypto.randomUUID()}`,
    occurred_at: new Date().toISOString(), phase, reason,
    path: /^\/[a-zA-Z0-9_/-]{0,120}$/.test(path) ? path : "/",
    elapsed_ms: Math.min(120_000, Math.max(0, Math.round(performance.now() - started))),
    http_status: status ?? null,
    request_ids: [...(run?.requestIds ?? [])],
    ...(run ? { evidence: {
      version: 2 as const,
      location: authFailureLocation(check, run.requests, reason),
      checks: run.checks.map((check) => ({ ...check })),
      requests: run.requests.map((request) => ({ ...request })),
      environment: { online: navigator.onLine, visibility: typeof document === "undefined" ? "unknown" as const : document.visibilityState, service_worker_controlled: Boolean(navigator.serviceWorker?.controller) },
    } } : {}),
  };
}

export function reportAuthDiagnostic(diagnostic: AuthDiagnostic) {
  // Reporting must not wait behind the session interceptor being diagnosed.
  void (diagnosticTransport ?? fetch)("/diagnostics/auth", {
    method: "POST", credentials: "omit", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(diagnostic), keepalive: true,
  }).catch(() => undefined);
}

export function configureAuthDiagnosticTransport(transport: typeof fetch) {
  diagnosticTransport ??= transport;
}
