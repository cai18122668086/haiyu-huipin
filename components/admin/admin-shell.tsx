"use client";

import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { AdminNavigation } from "./admin-navigation";
import styles from "./admin-shell.module.css";

export function AdminShell({ children }: { children: ReactNode }) {
  return (
    <AppShell sidebar={false}>
      <div className={styles.shell}>
        <AdminNavigation />
        {children}
      </div>
    </AppShell>
  );
}
