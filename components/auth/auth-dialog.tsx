"use client";

import { useEffect, useRef, useState, type MouseEvent } from "react";
import { LoginForm } from "@/features/auth/components/login-form";
import { RegisterForm } from "@/features/auth/components/register-form";
import { ForgotPasswordForm } from "@/features/auth/components/password-reset-form";
import { useBodyScrollLock } from "@/lib/hooks/use-body-scroll-lock";
import { AUTH_ROUTES } from "@/lib/auth/routes";
import styles from "./auth-shell.module.css";

export function AuthDialog({ returnTo, onClose }: { returnTo: string; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [view, setView] = useState<"login" | "register" | "reset">("login");
  useBodyScrollLock(true);
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => { dialog?.close(); };
  }, []);

  function switchView(event: MouseEvent<HTMLDialogElement>) {
    const link = (event.target as Element).closest<HTMLAnchorElement>("a[href]");
    if (!link || link.target === "_blank") return;
    const path = new URL(link.href).pathname;
    if (![AUTH_ROUTES.login, AUTH_ROUTES.register, AUTH_ROUTES.forgotPassword].includes(path as typeof AUTH_ROUTES.login)) return;
    event.preventDefault();
    event.stopPropagation();
    setView(path === AUTH_ROUTES.register ? "register" : path === AUTH_ROUTES.forgotPassword ? "reset" : "login");
  }

  const title = view === "register" ? "创建账号" : view === "reset" ? "找回密码" : "登录";
  return <dialog ref={ref} className={`${styles.panel} ${styles.dialog}`} aria-labelledby="login-dialog-title" onClickCapture={switchView}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { const r = event.currentTarget.getBoundingClientRect(); if (event.target === event.currentTarget && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom)) onClose(); }}>
    <button type="button" className={styles.close} onClick={onClose} aria-label="关闭登录">×</button>
    <div className={styles.heading}><h2 id="login-dialog-title">{title}</h2>{view === "login" && <p>让每一个好商品，都有一张好图片。</p>}</div>
    {view === "register" ? <RegisterForm returnTo={returnTo} /> : view === "reset" ? <ForgotPasswordForm returnTo={returnTo} /> : <LoginForm returnTo={returnTo} />}
    <p className={styles.brandNote}>海宇绘品 · HAIYU HUIPIN</p>
  </dialog>;
}
