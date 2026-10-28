import EmailVerification from "supertokens-web-js/recipe/emailverification";
import Session from "supertokens-web-js/recipe/session";
import Passwordless from "supertokens-web-js/recipe/passwordless";

import { initializeAuthClient } from "@/lib/auth/client";
import { isLocalDevelopmentAuthEnabled, LOCAL_DEV_AUTH_USER_ID } from "@/lib/auth/local-dev";
import type { AuthTraceContext } from "@/lib/auth/diagnostics";
import { emitAuthSessionChanged } from "@/lib/auth/session-events";

export type AuthFieldError = { id: string; error: string };
const PASSWORD_ERROR_MESSAGES: Record<string, string> = {
  "Password must contain at least 8 characters, including a number": "密码至少需要 8 位，并包含至少一个数字。",
  "Password's length must be lesser than 100 characters": "密码不能超过 99 位。",
  "Password must contain at least one alphabet": "密码至少需要包含一个英文字母。",
  "Password must contain at least one number": "密码至少需要包含一个数字。",
};
const initialise = () => initializeAuthClient();

export function normalizeLoginPhone(phone: string): string {
  const digits = phone.trim().replace(/^\+86/, "");
  if (!/^1[3-9]\d{9}$/.test(digits)) throw new Error("请输入有效的中国大陆手机号。");
  return `+86${digits}`;
}

export async function sendPhoneLoginCode(phone: string, resend = false) {
  if (isLocalDevelopmentAuthEnabled()) throw new Error("短信登录需要连接账号服务，请使用短信登录预览地址。");
  initialise();
  if (resend) return Passwordless.resendCode();
  const result = await Passwordless.createCode({ phoneNumber: normalizeLoginPhone(phone), shouldTryLinkingWithSessionUser: false });
  if (result.status === "OK") {
    await Passwordless.setLoginAttemptInfo({ attemptInfo: {
      deviceId: result.deviceId,
      preAuthSessionId: result.preAuthSessionId,
      flowType: result.flowType,
      shouldTryLinkingWithSessionUser: false,
    } });
  }
  return result;
}

export async function signInWithPhoneCode(code: string, flow: "login" | "register" | "reset" = "login", password?: string) {
  initialise();
  const result = await Passwordless.consumeCode({
    userInputCode: code,
    options: { preAPIHook: async ({ url, requestInit }) => ({
      url,
      requestInit: { ...requestInit, body: JSON.stringify({ ...JSON.parse(String(requestInit.body)), acceptedAgreements: true, flow, password }) },
    }) },
  });
  if (result.status === "OK") {
    // Authentication has already succeeded; unavailable browser storage must
    // not turn that success into an error or leave the dialog open.
    try { await Passwordless.clearLoginAttemptInfo(); } catch { /* Best-effort cleanup. */ }
    emitAuthSessionChanged(true);
  }
  return result;
}

async function phoneAccountRequest(path: string, body?: unknown) {
  initialise();
  const response = await fetch(`/api/v1/auth/phone/${path}`, {
    method: body === undefined ? "GET" : "POST", credentials: "include",
    headers: { "Content-Type": "application/json", "rid": "session" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(20_000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message || "请求未能完成，请稍后重试。");
  return result;
}

export async function signInWithPhonePassword(phone: string, password: string) {
  const result = await phoneAccountRequest("password/login", { phone: normalizeLoginPhone(phone), password });
  emitAuthSessionChanged(true);
  return result;
}

export async function changePhonePassword(currentPassword: string, newPassword: string) {
  return phoneAccountRequest("password/change", { current_password: currentPassword, new_password: newPassword });
}

export async function getPhoneProfile(): Promise<{phone_hint: string; has_password: boolean}> {
  return phoneAccountRequest("profile");
}

export async function sessionExists(trace?: AuthTraceContext) {
  if (isLocalDevelopmentAuthEnabled()) return true;
  initialise();
  return Session.doesSessionExist(trace ? { userContext: { polarisAuthTrace: trace } } : undefined);
}
export async function getSessionUserId() {
  if (isLocalDevelopmentAuthEnabled()) return LOCAL_DEV_AUTH_USER_ID;
  initialise();
  return Session.getUserId();
}
export async function signOut() {
  if (isLocalDevelopmentAuthEnabled()) return;
  initialise();
  await Session.signOut();
}
export async function isEmailVerified(trace?: AuthTraceContext) {
  if (isLocalDevelopmentAuthEnabled()) return true;
  initialise();
  return (await EmailVerification.isEmailVerified(trace ? { userContext: { polarisAuthTrace: trace } } : undefined)).isVerified;
}
export async function verifyEmail() {
  initialise();
  return EmailVerification.verifyEmail();
}
export async function sendVerificationEmail() {
  initialise();
  return EmailVerification.sendVerificationEmail();
}
export function authErrorMessage(error: unknown, fallback = "请求未能完成，请稍后重试。"): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
export function fieldErrorsToMap(errors: AuthFieldError[]): Record<string, string> {
  return Object.fromEntries(errors.map((field) => [field.id, PASSWORD_ERROR_MESSAGES[field.error] ?? field.error]));
}
