"use client";
import Link from "next/link";
import { AUTH_ROUTES } from "@/lib/auth/routes";
import { PhoneLoginForm } from "./phone-login-form";
export function ForgotPasswordForm({ returnTo = "/account" }: { returnTo?: string }) {
  return <><PhoneLoginForm flow="reset" returnTo={returnTo} /><p className="auth-form__switch"><Link href={AUTH_ROUTES.login}>返回登录</Link></p></>;
}
export function ResetPasswordForm() { return <ForgotPasswordForm />; }
