import { AuthShell } from "@/components/auth/auth-shell";
import { ForgotPasswordForm } from "@/features/auth/components/password-reset-form";

export default function ForgotPasswordPage() {
  return (
    <AuthShell title="找回密码" description="验证手机号，重新设置登录密码。">
      <ForgotPasswordForm />
    </AuthShell>
  );
}
