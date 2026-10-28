"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut, Smartphone, ShieldCheck, UserRound } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { getPhoneProfile, getSessionUserId, signOut } from "@/features/auth/api/auth";
import { ChangePasswordForm } from "./change-password-form";
import { authUserLabel, useCurrentAuthUser } from "@/features/auth/api/me";
import { AUTH_ROUTES } from "@/lib/auth/routes";
import { getAdminAccess } from "@/features/admin/api/admin";

export function AccountPanel() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const currentUser = useCurrentAuthUser();
  const phoneProfile = useQuery({ queryKey: ["auth", "phone-profile"], queryFn: getPhoneProfile, retry: false });
  const adminAccess = useQuery({
    queryKey: ["admin", "access"],
    queryFn: ({ signal }) => getAdminAccess(signal),
    staleTime: 5 * 60_000,
    retry: false,
  });
  const [sessionUserId, setSessionUserId] = useState<string>();
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState<string>();

  async function handleSignOut() {
    setSigningOut(true);
    setError(undefined);
    try {
      await signOut();
      queryClient.clear();
      router.replace(AUTH_ROUTES.login);
      router.refresh();
    } catch {
      setError("退出登录失败，请稍后重试。");
      setSigningOut(false);
    }
  }

  async function revealSessionUserId() {
    try {
      setSessionUserId(await getSessionUserId());
    } catch {
      setError("暂时无法读取会话信息。");
    }
  }

  if (currentUser.isPending) {
    return <div className="auth-inline-status">正在载入账户信息…</div>;
  }

  if (currentUser.isError) {
    return (
      <div className="auth-inline-status" role="alert">
        <p>账户信息载入失败。</p>
        <button
          className="auth-button auth-button--secondary"
          onClick={() => void currentUser.refetch()}
          type="button"
        >
          重新载入
        </button>
      </div>
    );
  }

  const user = currentUser.data;

  return (
    <section className="account-card" aria-labelledby="account-heading">
      <div className="account-card__heading">
        <span className="account-card__avatar" aria-hidden="true">
          <UserRound size={24} />
        </span>
        <div>
          <p>账户与安全</p>
          <h1 id="account-heading">{authUserLabel(user)}</h1>
        </div>
      </div>

      <dl className="account-details">
        <div>
          <dt><Smartphone size={16} /> 手机号</dt>
          <dd>{phoneProfile.data?.phone_hint || "尚未绑定"}</dd>
        </div>
        <div>
          <dt><ShieldCheck size={16} /> 登录密码</dt>
          <dd>{phoneProfile.data?.has_password ? "已设置" : "尚未设置"}</dd>
        </div>
        <div>
          <dt>账户 ID</dt>
          <dd>{user.id}</dd>
        </div>
        <div>
          <dt>会话用户 ID</dt>
          <dd>
            {sessionUserId || (
              <button
                className="account-inline-button"
                onClick={() => void revealSessionUserId()}
                type="button"
              >
                点击查看
              </button>
            )}
          </dd>
        </div>
      </dl>

      {error ? <p className="auth-form__error" role="alert">{error}</p> : null}
      {phoneProfile.data?.has_password ? <ChangePasswordForm /> : <p><Link href={AUTH_ROUTES.forgotPassword}>通过短信验证设置密码</Link></p>}

      <div className="account-card__actions">
        {adminAccess.data?.can_manage ? (
          <Link className="auth-button auth-button--secondary" href="/admin">
            管理后台
          </Link>
        ) : null}
        <button
          className="auth-button auth-button--secondary"
          disabled={signingOut}
          onClick={() => void handleSignOut()}
          type="button"
        >
          <LogOut size={17} />
          {signingOut ? "正在退出…" : "退出登录"}
        </button>
      </div>
    </section>
  );
}
