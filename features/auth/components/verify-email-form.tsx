"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { authErrorMessage, isEmailVerified, sendVerificationEmail, sessionExists, verifyEmail } from "@/features/auth/api/auth";
import { readReturnTo } from "@/lib/auth/return-to";
import { AUTH_ROUTES } from "@/lib/auth/routes";

type VerificationState = "checking" | "pending" | "verified" | "invalid" | "signed-out" | "error";
export function VerifyEmailForm() {
  const router = useRouter(); const attempted = useRef(false); const [state, setState] = useState<VerificationState>("checking"); const [message, setMessage] = useState<string>(); const [sending, setSending] = useState(false);
  useEffect(() => {
    if (attempted.current) return; attempted.current = true; let cancelled = false;
    void (async () => { try {
      if (!(await sessionExists())) { if (!cancelled) setState("signed-out"); return; }
      if (new URLSearchParams(window.location.search).get("token")) { const result = await verifyEmail(); if (cancelled) return; if (result.status === "EMAIL_VERIFICATION_INVALID_TOKEN_ERROR") { setState("invalid"); return; } }
      const verified = await isEmailVerified(); if (!cancelled) setState(verified ? "verified" : "pending");
    } catch (error) { if (!cancelled) { setMessage(authErrorMessage(error)); setState("error"); } } })();
    return () => { cancelled = true; };
  }, []);
  async function resend() { setSending(true); setMessage(undefined); try { const result = await sendVerificationEmail(); if (result.status === "EMAIL_ALREADY_VERIFIED_ERROR") setState("verified"); else { setMessage("验证邮件已重新发送，请检查收件箱和垃圾邮件目录。"); setState("pending"); } } catch (error) { setMessage(authErrorMessage(error, "验证邮件发送失败，请稍后重试。")); } finally { setSending(false); } }
  if (state === "checking") return <p className="auth-inline-status">正在检查邮箱验证状态…</p>;
  if (state === "verified") return <div className="auth-result auth-result--success"><h2>邮箱验证成功</h2><p>账号安全验证已完成，可以进入创作后台。</p><button className="auth-button auth-button--primary auth-button--full" onClick={() => router.replace(readReturnTo(window.location.search))}>进入工作台</button></div>;
  if (state === "signed-out") return <div className="auth-result"><h2>请先登录</h2><p>验证邮箱需要有效的登录会话。登录后可继续验证。</p><Link className="auth-button auth-button--primary auth-button--full" href={AUTH_ROUTES.login}>前往登录</Link></div>;
  return <div className="auth-result"><h2>{state === "invalid" ? "验证链接已失效" : state === "error" ? "验证状态检查失败" : "请验证你的邮箱"}</h2><p>{state === "invalid" ? "该链接可能已经使用或已过期，请重新发送验证邮件。" : "我们会向注册邮箱发送验证链接，完成验证后才能访问业务页面。"}</p>{message ? <p className={state === "error" ? "auth-form__error" : "auth-form__notice"} role="status">{message}</p> : null}<button className="auth-button auth-button--primary auth-button--full" disabled={sending} onClick={() => void resend()}>{sending ? "正在发送…" : "重新发送验证邮件"}</button><Link className="auth-button auth-button--secondary auth-button--full" href={AUTH_ROUTES.account}>查看账户</Link></div>;
}
