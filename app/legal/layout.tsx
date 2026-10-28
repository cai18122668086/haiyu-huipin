import Link from "next/link";

import { BrandMark } from "@/components/brand-mark";
import { AUTH_ROUTES, LEGAL_ROUTES } from "@/lib/auth/routes";

export default function LegalLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="legal-page">
      <header className="legal-header">
        <Link className="legal-header__brand" href="/" aria-label="返回海宇绘品首页">
          <BrandMark />
        </Link>
        <nav className="legal-header__nav" aria-label="法律文件导航">
          <Link href={LEGAL_ROUTES.terms}>用户协议</Link>
          <Link href={LEGAL_ROUTES.privacy}>隐私政策</Link>
          <Link href={AUTH_ROUTES.register}>返回注册</Link>
        </nav>
      </header>
      <div className="legal-shell">{children}</div>
    </main>
  );
}