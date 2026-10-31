"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { authErrorMessage, normalizeLoginPhone, sendPhoneLoginCode, signInWithPhoneCode } from "@/features/auth/api/auth";
import { LEGAL_ROUTES } from "@/lib/auth/routes";

export function PhoneLoginForm({ returnTo = "/", flow = "login" }: { returnTo?: string; flow?: "login" | "register" | "reset" }) {
  const router = useRouter();
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [sentPhone, setSentPhone] = useState<string>();
  const [accepted, setAccepted] = useState(false);
  const [sending, setSending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const submissionLocked = useRef(false);
  const [completed, setCompleted] = useState(false);
  const needsPassword = flow !== "login";
  const validPassword = password.length >= 8 && password.length <= 99 && /[A-Za-z]/.test(password) && /[0-9]/.test(password);
  const busy = sending || submitting || completed;
  const validPhone = /^1[3-9]\d{9}$/.test(phone);

  useEffect(() => {
    if (remaining <= 0) return;
    const timer = setTimeout(() => setRemaining((value) => Math.max(0, value - 1)), 1000);
    return () => clearTimeout(timer);
  }, [remaining]);

  async function sendCode() {
    setError(undefined);
    setNotice(undefined);
    if (!accepted) { setError("请先阅读并同意用户协议和隐私政策。"); return; }
    setSending(true);
    try {
      const normalized = normalizeLoginPhone(phone);
      const result = await sendPhoneLoginCode(phone, sentPhone === normalized, flow);
      if (result.status === "SIGN_IN_UP_NOT_ALLOWED") { setError(result.reason); return; }
      if (result.status === "RESTART_FLOW_ERROR") { setSentPhone(undefined); setError("验证码会话已过期，请重新获取。"); return; }
      setSentPhone(normalized);
      setCode("");
      setRemaining(60);
      setNotice(`验证码已发送至 ${phone.slice(0, 3)}****${phone.slice(-4)}，请查收短信。`);
    } catch (caught) {
      setError(authErrorMessage(caught, "短信发送失败，请稍后重试。"));
    } finally { setSending(false); }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submissionLocked.current) return;
    setError(undefined);
    if (!accepted) { setError("请先阅读并同意用户协议和隐私政策。"); return; }
    if (!sentPhone || sentPhone !== normalizeLoginPhone(phone)) { setError("请先获取当前手机号的验证码。"); return; }
    if (needsPassword && (!validPassword || password !== confirmation)) { setError("请设置包含字母和数字的 8 至 99 位密码，并确保两次输入一致。"); return; }
    submissionLocked.current = true;
    setSubmitting(true);
    try {
      const result = await signInWithPhoneCode(code, flow, needsPassword ? password : undefined);
      if (result.status === "OK") { setCompleted(true); router.replace(returnTo); router.refresh(); return; }
      submissionLocked.current = false;
      if (result.status === "SIGN_IN_UP_NOT_ALLOWED") setError(result.reason);
      else if (result.status === "RESTART_FLOW_ERROR") { setSentPhone(undefined); setCode(""); setNotice(undefined); setRemaining(0); setError("验证码已使用或会话已失效，请重新获取验证码。"); }
      else if (result.status === "EXPIRED_USER_INPUT_CODE_ERROR") setError("验证码已过期，请重新获取。");
      else setError(`验证码不正确，还可尝试 ${Math.max(0, result.maximumCodeInputAttempts - result.failedCodeInputAttemptCount)} 次。`);
    } catch (caught) { submissionLocked.current = false; setError(authErrorMessage(caught, "登录失败，请稍后重试。")); }
    finally { setSubmitting(false); }
  }

  return <form className="auth-form sms-login-form" onSubmit={submit}>
    <label className="auth-field">
      <span className="sms-field-label">手机号</span>
      <div className="sms-phone-input"><span aria-hidden="true">+86</span><input aria-label="手机号" autoComplete="tel-national" inputMode="tel" maxLength={11} placeholder="请输入手机号" required type="tel" value={phone} disabled={busy} onChange={(event) => { setPhone(event.target.value.replace(/\D/g, "").slice(0, 11)); setError(undefined); setNotice(undefined); }} /></div>
    </label>
    <label className="auth-field">
      <span className="sms-field-label">验证码</span>
      <div className="sms-code-input"><input aria-label="验证码" autoComplete="one-time-code" inputMode="numeric" maxLength={6} placeholder="请输入验证码" required pattern="[0-9]{6}" value={code} disabled={busy} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} /><button type="button" disabled={!validPhone || remaining > 0 || busy} onClick={() => void sendCode()}>{sending ? "发送中…" : remaining > 0 ? `${remaining}s 后重发` : "获取验证码"}</button></div>
    </label>
    {needsPassword ? <>
      <label className="auth-field"><span>设置密码</span><input aria-label="设置密码" autoComplete="new-password" type="password" minLength={8} maxLength={99} placeholder="8–99 位，包含字母和数字" required value={password} disabled={busy} onChange={(event) => setPassword(event.target.value)} /></label>
      <label className="auth-field"><span>确认密码</span><input aria-label="确认密码" autoComplete="new-password" type="password" maxLength={99} placeholder="请再次输入密码" required value={confirmation} disabled={busy} onChange={(event) => setConfirmation(event.target.value)} /></label>
    </> : null}
    {notice ? <p className="sms-notice" role="status">{notice}</p> : null}
    {error ? <p className="auth-form__error" role="alert">{error}</p> : null}
    <button className="auth-button auth-button--primary auth-button--full" disabled={!validPhone || code.length !== 6 || !accepted || busy || !sentPhone || (needsPassword && (!validPassword || password !== confirmation))} type="submit">{submitting ? "正在验证…" : flow === "register" ? "注册并登录" : flow === "reset" ? "重置密码" : "登录 / 注册"}</button>
    <label className="sms-consent"><input type="checkbox" checked={accepted} disabled={busy} onChange={(event) => setAccepted(event.target.checked)} /><span>我已阅读并同意<Link href={LEGAL_ROUTES.terms} target="_blank" rel="noreferrer">《用户协议》</Link>和<Link href={LEGAL_ROUTES.privacy} target="_blank" rel="noreferrer">《隐私政策》</Link></span></label>
  </form>;
}
