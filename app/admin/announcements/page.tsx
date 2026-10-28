"use client";

import { useState } from "react";
import { AdminShell } from "@/components/admin/admin-shell";
import { AnnouncementAdmin } from "@/features/announcements/components/announcement-admin";

export default function AdminAnnouncementsPage() {
  const [filter, setFilter] = useState<"all" | "draft" | "published" | "archived">("all");
  return (
    <AdminShell>
      <AnnouncementAdmin filter={filter} onFilterChange={setFilter} />
    </AdminShell>
  );
}
