import { AuthShell } from "@/components/auth/auth-shell";
import { RegisterForm } from "@/features/auth/components/register-form";
import { sanitizeReturnTo } from "@/lib/auth/return-to";

type RegisterPageProps = {
  searchParams: Promise<{ returnTo?: string | string[] }>;
};

export default async function RegisterPage({ searchParams }: RegisterPageProps) {
  const value = (await searchParams).returnTo;
  const returnTo = sanitizeReturnTo(Array.isArray(value) ? value[0] : value);

  return (
    <AuthShell title="创建账号">
      <RegisterForm returnTo={returnTo} />
    </AuthShell>
  );
}
