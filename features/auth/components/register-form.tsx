"use client";
import Link from "next/link";
import { AUTH_ROUTES } from "@/lib/auth/routes";
import { withReturnTo } from "@/lib/auth/return-to";
import { PhoneLoginForm } from "./phone-login-form";
export function RegisterForm({ returnTo = "/" }: { returnTo?: string }) {
  return <><PhoneLoginForm flow="register" returnTo={returnTo} /><p className="auth-form__switch">已有账号？<Link href={withReturnTo(AUTH_ROUTES.login, returnTo)}>立即登录</Link></p></>;
}
