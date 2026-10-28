import { AuthShell } from "@/components/auth/auth-shell";
import { VerifyEmailForm } from "@/features/auth/components/verify-email-form";

export default function VerifyEmailPage() {
  return (
    <AuthShell title="验证邮箱" description="验证邮箱后即可安全访问你的工作区。">
      <VerifyEmailForm />
    </AuthShell>
  );
}
