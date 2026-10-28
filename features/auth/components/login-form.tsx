"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { authErrorMessage, signInWithPhonePassword } from "@/features/auth/api/auth";
import { AUTH_ROUTES, LEGAL_ROUTES } from "@/lib/auth/routes";
import { withReturnTo } from "@/lib/auth/return-to";
import { PhoneLoginForm } from "./phone-login-form";

export function LoginForm({ returnTo = "/" }: { returnTo?: string }) {
  const [method, setMethod] = useState<"code" | "password">("code");
  return <>
    <div className="auth-method-tabs" aria-label="登录方式">
      <button type="button" aria-pressed={method === "code"} onClick={() => setMethod("code")}>验证码登录</button>
      <button type="button" aria-pressed={method === "password"} onClick={() => setMethod("password")}>密码登录</button>
    </div>
    {method === "code" ? <PhoneLoginForm returnTo={returnTo} /> : <PasswordLoginForm returnTo={returnTo} />}
    <p className="auth-form__switch">还没有账号？<Link href={withReturnTo(AUTH_ROUTES.register, returnTo)}>立即注册</Link></p>
  </>;
}

function PasswordLoginForm({ returnTo }: { returnTo: string }) {
  const router = useRouter();
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!accepted) return;
    setBusy(true); setError(undefined);
    try { await signInWithPhonePassword(phone, password); router.replace(returnTo); router.refresh(); }
    catch (caught) { setError(authErrorMessage(caught, "登录失败，请稍后重试。")); }
    finally { setBusy(false); }
  }

  return <form className="auth-form" onSubmit={submit}>
    <label className="auth-field"><span className="sms-field-label">手机号</span><div className="sms-phone-input"><span aria-hidden="true">+86</span><input aria-label="手机号" type="tel" inputMode="tel" autoComplete="username" maxLength={11} placeholder="请输入手机号" value={phone} required disabled={busy} onChange={(event) => setPhone(event.target.value.replace(/\D/g, "").slice(0, 11))} /></div></label>
    <label className="auth-field"><span className="sms-field-label">密码</span><input aria-label="密码" type="password" autoComplete="current-password" maxLength={99} placeholder="请输入密码" value={password} required disabled={busy} onChange={(event) => setPassword(event.target.value)} /></label>
    <div className="auth-form__aside"><Link href={AUTH_ROUTES.forgotPassword}>忘记密码？</Link></div>
    {error ? <p className="auth-form__error" role="alert">{error}</p> : null}
    <button className="auth-button auth-button--primary" disabled={busy || !/^1[3-9]\d{9}$/.test(phone) || !password || !accepted}>{busy ? "正在登录…" : "登录"}</button>
    <label className="sms-consent"><input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} /><span>我已阅读并同意<Link href={LEGAL_ROUTES.terms} target="_blank" rel="noreferrer">《用户协议》</Link>和<Link href={LEGAL_ROUTES.privacy} target="_blank" rel="noreferrer">《隐私政策》</Link></span></label>
  </form>;
}
