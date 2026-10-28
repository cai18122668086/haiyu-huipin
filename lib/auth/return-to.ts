import { AUTH_ROUTES, isAuthOrLegalRoute } from "./routes";

const RETURN_TO_FALLBACK = "/";
const LOCAL_ORIGIN = "https://app.local";

export function sanitizeReturnTo(value: string | null | undefined, fallback = RETURN_TO_FALLBACK): string {
  if (!value) return fallback;
  const candidate = value.trim();
  if (!candidate.startsWith("/") || candidate.startsWith("//")) return fallback;
  try {
    const decodedPath = decodeURIComponent(candidate.split(/[?#]/, 1)[0]);
    if (decodedPath.includes("\\") || decodedPath.startsWith("//")) return fallback;
    const parsed = new URL(candidate, LOCAL_ORIGIN);
    if (parsed.origin !== LOCAL_ORIGIN || isAuthOrLegalRoute(parsed.pathname)) return fallback;
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return fallback;
  }
}

export function currentReturnTo(): string {
  if (typeof window === "undefined") return RETURN_TO_FALLBACK;
  return sanitizeReturnTo(`${window.location.pathname}${window.location.search}${window.location.hash}`);
}

export function readReturnTo(search: string): string {
  return sanitizeReturnTo(new URLSearchParams(search).get("returnTo"));
}

export function withReturnTo(pathname: string, returnTo: string): string {
  const params = new URLSearchParams({ returnTo: sanitizeReturnTo(returnTo) });
  return `${pathname}?${params.toString()}`;
}

export const loginHref = (returnTo: string) => withReturnTo(AUTH_ROUTES.login, returnTo);
export const verifyEmailHref = (returnTo: string) => withReturnTo(AUTH_ROUTES.verifyEmail, returnTo);
