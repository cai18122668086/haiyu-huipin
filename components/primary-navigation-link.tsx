"use client";
import Link from "next/link";
import { Flame } from "lucide-react";
import { useId, type CSSProperties } from "react";
import styles from "./primary-navigation-link.module.css";

export function PrimaryNavigationLink({ href, label, active, onClick, style, mobile = false }: {
  href: string; label: string; active: boolean; onClick?: () => void; style?: CSSProperties; mobile?: boolean;
}) {
  const tooltipId = useId();
  const replica = href === "/style-replica";
  const hot = replica || href === "/inspiration";
  return <Link href={href} onClick={onClick} style={style}
    className={`${styles.link}${active ? " active" : ""}`}
    aria-current={active ? "page" : undefined}
    aria-describedby={replica ? tooltipId : undefined}>
    {hot && <Flame className={mobile ? undefined : "main-nav__flame"} size={14} fill="currentColor" aria-hidden="true" style={mobile ? { color: "#8d79ad", verticalAlign: "-2px", marginRight: 4 } : undefined} />}
    {label}
    {replica && <span id={tooltipId} role="tooltip" className={styles.tooltip}>全网独家，好用到你害怕</span>}
  </Link>;
}
