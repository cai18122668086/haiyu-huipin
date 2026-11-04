import { AUTH_WEBSITE_BASE_PATH } from "./config";
import { getToolSeoPage } from "../seo/pages";

export const AUTH_ROUTES = {
  login: `${AUTH_WEBSITE_BASE_PATH}/login`,
  register: `${AUTH_WEBSITE_BASE_PATH}/register`,
  verifyEmail: `${AUTH_WEBSITE_BASE_PATH}/verify-email`,
  forgotPassword: `${AUTH_WEBSITE_BASE_PATH}/forgot-password`,
  resetPassword: `${AUTH_WEBSITE_BASE_PATH}/reset-password`,
  account: "/account",
} as const;

export const LEGAL_ROUTES = {
  terms: "/legal/terms",
  privacy: "/legal/privacy",
} as const;

export function isAuthOrLegalRoute(pathname: string): boolean {
  return pathname === AUTH_WEBSITE_BASE_PATH ||
    pathname.startsWith(`${AUTH_WEBSITE_BASE_PATH}/`) ||
    pathname === "/legal" ||
    pathname.startsWith("/legal/");
}

export function routeRequiresWorkspace(pathname: string): boolean {
  return !isPublicRoute(pathname) && pathname !== AUTH_ROUTES.account;
}

// Known tools have public introductions, while personal and unknown routes stay protected.
export function isPublicRoute(pathname: string): boolean {
  return ["/", "/tools", "/help", "/inspiration"].includes(pathname) ||
    Boolean(getToolSeoPage(pathname)) ||
    isAuthOrLegalRoute(pathname);
}
