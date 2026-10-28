import { AuthShell } from "@/components/auth/auth-shell";
import { LoginForm } from "@/features/auth/components/login-form";
import { sanitizeReturnTo } from "@/lib/auth/return-to";

type LoginPageProps = {
  searchParams: Promise<{ returnTo?: string | string[] }>;
};

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const value = (await searchParams).returnTo;
  const returnTo = sanitizeReturnTo(Array.isArray(value) ? value[0] : value);

  return (
    <AuthShell title="登录" description="让每一个好商品，都有一张好图片。">
      <LoginForm returnTo={returnTo} />
    </AuthShell>
  );
}
