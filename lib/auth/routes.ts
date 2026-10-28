import { AUTH_WEBSITE_BASE_PATH } from "./config";

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

// 展示页允许游客浏览；创作入口、个人数据和未知路径仍默认要求登录。
export function isPublicRoute(pathname: string): boolean {
  return ["/", "/tools", "/help", "/inspiration"].includes(pathname) ||
    isAuthOrLegalRoute(pathname);
}
