"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { authErrorMessage, changePhonePassword, signOut } from "@/features/auth/api/auth";
import { AUTH_ROUTES } from "@/lib/auth/routes";

export function ChangePasswordForm() {
  const router = useRouter();
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const valid = password.length >= 8 && password.length <= 99 && /[A-Za-z]/.test(password) && /[0-9]/.test(password);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(undefined);
    if (!valid || password !== confirmation) { setError("新密码须包含字母和数字，且两次输入一致。"); return; }
    setBusy(true);
    try {
      await changePhonePassword(current, password);
      await signOut();
      router.replace(`${AUTH_ROUTES.login}?passwordChanged=1`); router.refresh();
    } catch (caught) { setError(authErrorMessage(caught, "密码修改失败，请稍后重试。")); }
    finally { setBusy(false); }
  }
  return <form className="auth-form" onSubmit={submit} style={{maxWidth: 430, margin: "24px 0"}}>
    <h2 style={{fontSize: 18}}>修改登录密码</h2>
    <label className="auth-field"><span>当前密码</span><input type="password" autoComplete="current-password" maxLength={99} required value={current} disabled={busy} onChange={(event) => setCurrent(event.target.value)} /></label>
    <label className="auth-field"><span>新密码</span><input type="password" autoComplete="new-password" placeholder="8–99 位，包含字母和数字" maxLength={99} required value={password} disabled={busy} onChange={(event) => setPassword(event.target.value)} /></label>
    <label className="auth-field"><span>确认新密码</span><input type="password" autoComplete="new-password" maxLength={99} required value={confirmation} disabled={busy} onChange={(event) => setConfirmation(event.target.value)} /></label>
    {error ? <p className="auth-form__error" role="alert">{error}</p> : null}
    <p className="auth-field__hint">修改后需要重新登录，其他设备上的登录也会失效。</p>
    <button className="auth-button auth-button--primary" disabled={busy || !current || !valid || password !== confirmation}>{busy ? "正在修改…" : "确认修改"}</button>
  </form>;
}
