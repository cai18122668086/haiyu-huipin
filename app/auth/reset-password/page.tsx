import { AuthShell } from "@/components/auth/auth-shell";
import { ResetPasswordForm } from "@/features/auth/components/password-reset-form";

export default function ResetPasswordPage() {
  return (
    <AuthShell title="设置新密码" description="请设置一个安全且未在其他网站使用过的新密码。">
      <ResetPasswordForm />
    </AuthShell>
  );
}
