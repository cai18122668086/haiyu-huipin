"use client";
import Link from "@/components/seo-link";
import { AUTH_ROUTES } from "@/lib/auth/routes";
import { withReturnTo } from "@/lib/auth/return-to";
import { PhoneLoginForm } from "./phone-login-form";
import { usePublicReplicaWelcome } from "@/features/billing/api/replica-welcome";
import { ReplicaWelcomeOffer } from "@/components/replica-welcome-offer";
export function RegisterForm({ returnTo = "/", showOffer = true }: { returnTo?: string; showOffer?: boolean }) {
  const promotion = usePublicReplicaWelcome();
  const target = returnTo === "/" && promotion.data?.enabled ? "/style-replica?welcome=1" : returnTo;
  return <>{showOffer && promotion.data?.enabled && <ReplicaWelcomeOffer compact />}<PhoneLoginForm flow="register" returnTo={target} /><p className="auth-form__switch">已有账号？<Link href={withReturnTo(AUTH_ROUTES.login, target)}>立即登录</Link></p></>;
}
