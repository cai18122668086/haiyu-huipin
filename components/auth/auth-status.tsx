"use client";
import { LoaderCircle, ShieldCheck } from "lucide-react";
import styles from "./auth-status.module.css";

export function AuthStatus({ title, description, busy = false, children }: { title: string; description: string; busy?: boolean; children?: React.ReactNode }) {
  return <main className={`auth-status-page ${styles.page}`}><section className="auth-status" aria-live="polite">
    <span className="auth-status__icon" aria-hidden="true">{busy ? <LoaderCircle className="auth-spin" size={24} /> : <ShieldCheck size={24} />}</span>
    <h1>{title}</h1><p>{description}</p>{children ? <div className="auth-status__actions">{children}</div> : null}
  </section></main>;
}
