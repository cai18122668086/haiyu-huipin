import type { AuthFailureReason, AuthPhase } from "./diagnostic-payload";

export const AUTH_LOCATION_LABELS = {
  AUTH_BEFORE_REQUEST: "SDK 尚未准备请求",
  AUTH_BEFORE_FETCH: "请求已准备，尚未进入网络调用",
  AUTH_WAIT_HEADERS: "网络调用尚未返回响应头",
  AUTH_WAIT_BODY: "已收到响应，响应体尚未读完",
  AUTH_AFTER_HEADERS: "已收到响应，SDK 处理尚未完成",
  AUTH_AFTER_BODY: "响应体已读完，SDK 检查尚未完成",
  AUTH_REFRESH_PENDING: "会话刷新尚未完成（传输阶段未观测）",
  AUTH_REQUEST_ERROR: "网络调用或响应体读取报错",
  AUTH_HTTP_ERROR: "认证接口返回错误状态",
  AUTH_UNCLASSIFIED: "当前证据不足以细分",
} as const;
export type AuthLocation = keyof typeof AUTH_LOCATION_LABELS;
export type AuthErrorName = "AbortError" | "TimeoutError" | "TypeError" | "SyntaxError" | "Error" | "Other";
export type AuthCheckTrace = {
  attempt: number;
  phase: AuthPhase;
  started_ms: number;
  finished_ms?: number;
  timed_out_ms?: number;
  outcome: "pending" | "resolved" | "rejected";
  error_name?: AuthErrorName;
};
export type AuthRequestTrace = {
  id: string;
  server_id?: string;
  attempt: number;
  phase: AuthPhase;
  endpoint: "email_status" | "session_refresh" | "other_auth";
  prepared_ms: number;
  fetch_started_ms?: number;
  headers_received_ms?: number;
  interceptor_finished_ms?: number;
  sdk_response_ms?: number;
  body_started_ms?: number;
  body_finished_ms?: number;
  aborted_ms?: number;
  failed_ms?: number;
  http_status?: number;
  error_name?: AuthErrorName;
  body_observer_unavailable?: boolean;
};
export type AuthEvidence = {
  version: 2;
  location: AuthLocation;
  checks: AuthCheckTrace[];
  requests: AuthRequestTrace[];
  environment: {
    online: boolean;
    visibility: "visible" | "hidden" | "unknown";
    service_worker_controlled: boolean;
  };
};

const validId = (value: unknown): value is string => typeof value === "string" && /^[a-zA-Z0-9_-]{1,80}$/.test(value);
const validTime = (value: unknown): value is number => Number.isInteger(value) && Number(value) >= 0 && Number(value) <= 120_000;
const object = (value: unknown): Record<string, unknown> | undefined => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
const phases = ["session", "email_verification"];
const errors = ["AbortError", "TimeoutError", "TypeError", "SyntaxError", "Error", "Other"];
const oneOf = (value: unknown, values: readonly string[]) => typeof value === "string" && values.includes(value);

// Rebuild every nested object. Never serialize arbitrary SDK objects, messages,
// headers, request/response bodies, URLs, cookies or userContext.
export function parseAuthEvidence(value: unknown): AuthEvidence | null {
  const data = object(value);
  if (!data || data.version !== 2 || typeof data.location !== "string" || !Object.hasOwn(AUTH_LOCATION_LABELS, data.location)) return null;
  if (!Array.isArray(data.checks) || data.checks.length > 4 || !Array.isArray(data.requests) || data.requests.length > 6) return null;
  const environment = object(data.environment);
  if (!environment || typeof environment.online !== "boolean" || typeof environment.service_worker_controlled !== "boolean" || !oneOf(environment.visibility, ["visible", "hidden", "unknown"])) return null;
  const checks: AuthCheckTrace[] = [];
  const requests: AuthRequestTrace[] = [];
  for (const value of data.checks) {
    const item = object(value);
    if (!item || ![1, 2].includes(Number(item.attempt)) || !Number.isInteger(item.attempt) || !oneOf(item.phase, phases) || !validTime(item.started_ms) || !oneOf(item.outcome, ["pending", "resolved", "rejected"])) return null;
    const check: AuthCheckTrace = { attempt: Number(item.attempt), phase: item.phase as AuthPhase, started_ms: item.started_ms, outcome: item.outcome as AuthCheckTrace["outcome"] };
    for (const key of ["finished_ms", "timed_out_ms"] as const) {
      if (item[key] !== undefined) { if (!validTime(item[key])) return null; check[key] = item[key]; }
    }
    if (item.error_name !== undefined) { if (!oneOf(item.error_name, errors)) return null; check.error_name = item.error_name as AuthErrorName; }
    checks.push(check);
  }
  for (const value of data.requests) {
    const item = object(value);
    if (!item || !validId(item.id) || ![1, 2].includes(Number(item.attempt)) || !Number.isInteger(item.attempt) || !oneOf(item.phase, phases) || !oneOf(item.endpoint, ["email_status", "session_refresh", "other_auth"]) || !validTime(item.prepared_ms)) return null;
    const request: AuthRequestTrace = { id: item.id, attempt: Number(item.attempt), phase: item.phase as AuthPhase, endpoint: item.endpoint as AuthRequestTrace["endpoint"], prepared_ms: item.prepared_ms };
    for (const key of ["fetch_started_ms", "headers_received_ms", "interceptor_finished_ms", "sdk_response_ms", "body_started_ms", "body_finished_ms", "aborted_ms", "failed_ms"] as const) {
      if (item[key] !== undefined) { if (!validTime(item[key])) return null; request[key] = item[key]; }
    }
    if (item.server_id !== undefined) { if (!validId(item.server_id)) return null; request.server_id = item.server_id; }
    if (item.http_status !== undefined) { if (!Number.isInteger(item.http_status) || Number(item.http_status) < 100 || Number(item.http_status) > 599) return null; request.http_status = Number(item.http_status); }
    if (item.error_name !== undefined) { if (!oneOf(item.error_name, errors)) return null; request.error_name = item.error_name as AuthErrorName; }
    if (item.body_observer_unavailable !== undefined) { if (typeof item.body_observer_unavailable !== "boolean") return null; request.body_observer_unavailable = item.body_observer_unavailable; }
    requests.push(request);
  }
  return { version: 2, location: data.location as AuthLocation, checks, requests, environment: { online: environment.online, visibility: environment.visibility as AuthEvidence["environment"]["visibility"], service_worker_controlled: environment.service_worker_controlled } };
}

export function authFailureLocation(check: AuthCheckTrace | undefined, requests: AuthRequestTrace[], reason: AuthFailureReason): AuthLocation {
  if (!check) return "AUTH_UNCLASSIFIED";
  const request = requests.filter((request) => request.attempt === check.attempt && request.phase === check.phase).at(-1);
  if (!request) return "AUTH_BEFORE_REQUEST";
  if (request.http_status !== undefined && request.http_status >= 400) return "AUTH_HTTP_ERROR";
  if (request.failed_ms !== undefined && reason !== "timeout") return "AUTH_REQUEST_ERROR";
  if (request.body_started_ms !== undefined && request.body_finished_ms === undefined && !request.body_observer_unavailable) return "AUTH_WAIT_BODY";
  if (request.body_finished_ms !== undefined) return "AUTH_AFTER_BODY";
  if (request.headers_received_ms !== undefined || request.sdk_response_ms !== undefined) return "AUTH_AFTER_HEADERS";
  if (request.fetch_started_ms !== undefined) return "AUTH_WAIT_HEADERS";
  // The pinned SDK refreshes through its private original-fetch reference,
  // bypassing the public fetch-interceptor override. Do not invent a stage.
  if (request.endpoint === "session_refresh") return "AUTH_REFRESH_PENDING";
  return "AUTH_BEFORE_FETCH";
}
