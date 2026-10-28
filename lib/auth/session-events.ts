export const AUTH_UNAUTHORISED_EVENT = "polaris:auth:unauthorised";
export type AuthUnauthorisedReason = "api-401" | "session" | "sign-out";

export function emitAuthUnauthorised(reason: AuthUnauthorisedReason): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(AUTH_UNAUTHORISED_EVENT, { detail: { reason } }));
}

export function onAuthUnauthorised(listener: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  window.addEventListener(AUTH_UNAUTHORISED_EVENT, listener);
  return () => window.removeEventListener(AUTH_UNAUTHORISED_EVENT, listener);
}

const AUTH_SESSION_CHANGED_EVENT = "polaris:auth:session-changed";
export function emitAuthSessionChanged(authenticated: boolean): void {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(AUTH_SESSION_CHANGED_EVENT, { detail: authenticated }));
}
export function onAuthSessionChanged(listener: (authenticated: boolean) => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  const handler = (event: Event) => listener((event as CustomEvent<boolean>).detail);
  window.addEventListener(AUTH_SESSION_CHANGED_EVENT, handler);
  return () => window.removeEventListener(AUTH_SESSION_CHANGED_EVENT, handler);
}

const AUTH_LOGIN_REQUESTED_EVENT = "polaris:auth:login-requested";
export function requestLogin(returnTo: string): void {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(AUTH_LOGIN_REQUESTED_EVENT, { detail: returnTo }));
}
export function onLoginRequested(listener: (returnTo: string) => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  const handler = (event: Event) => { const target = (event as CustomEvent<unknown>).detail; if (typeof target === "string") listener(target); };
  window.addEventListener(AUTH_LOGIN_REQUESTED_EVENT, handler);
  return () => window.removeEventListener(AUTH_LOGIN_REQUESTED_EVENT, handler);
}
