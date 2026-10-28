"use client";

import { ArrowLeft, ExternalLink, ShieldCheck, Sparkles, Workflow } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import styles from "./admin-shell.module.css";

const ADMIN_NAVIGATION = [
  { href: "/admin", label: "工作台", icon: ShieldCheck },
  { href: "/admin/billing", label: "用户与财务", icon: ShieldCheck },
  { href: "/admin/announcements", label: "公告管理", icon: ExternalLink },
  { href: "/admin/inspiration", label: "灵感案例", icon: Sparkles },
  { href: "/admin/accounts", label: "电商账号", icon: Workflow },
  { href: "/commerce-crawler", label: "真实爬虫", icon: Workflow },
] as const;

function isActivePath(pathname: string, href: string) {
  return pathname === href || (href !== "/admin" && pathname.startsWith(`${href}/`));
}

export function AdminNavigation() {
  const pathname = usePathname();
  return (
    <nav className={styles.navigation} aria-label="管理中心导航">
      {ADMIN_NAVIGATION.map(({ href, label, icon: Icon }) => {
        const active = isActivePath(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            className={active ? `${styles.link} ${styles.active}` : styles.link}
            aria-current={active ? "page" : undefined}
          >
            <Icon size={15} />
            {label}
          </Link>
        );
      })}
      <Link className={styles.back} href="/">
        <ArrowLeft size={15} /> 返回工作台首页
      </Link>
    </nav>
  );
}
