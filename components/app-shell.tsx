"use client";

import { useRef, useState } from "react";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { useCreditAccount } from "@/features/billing/api/credits";
import { TopNavigation } from "./top-navigation";
import { ToolSidebar } from "./tool-sidebar";
import { TaskDrawer } from "./task-drawer";
import { AnnouncementPrompt } from "./announcement-prompt";
import { usePathname } from "next/navigation";
import { getFeatureByRoute } from "@/lib/feature-registry";
import { CreationWorkspaceBar } from "./creation-workspace-bar";
import { CreditAccountHeader } from "@/features/billing/components/credit-account";

export function AppShell({
  children,
  flush = false,
  sidebar = true,
}: {
  children: React.ReactNode;
  flush?: boolean;
  sidebar?: boolean;
}) {
  const [taskOpen, setTaskOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const taskTriggerRef = useRef<HTMLButtonElement>(null);
  const pathname = usePathname();
  const { workspace } = useWorkspace();
  const credits = useCreditAccount(workspace?.id);
  const pilotAccount = pathname === "/account" && Boolean(credits.data?.enforcement_enabled);
  const feature = getFeatureByRoute(pathname);
  const creationId = feature?.id ?? (pathname === "/inspiration" ? "inspiration" : undefined);
  const showSidebar = sidebar && !creationId;

  return (
    <div className={`app-shell${creationId ? " app-shell--creation" : ""}${pathname === "/" ? " app-shell--home" : ""}`} data-feature={creationId}>
      {pilotAccount ? <CreditAccountHeader /> : <TopNavigation
        compactAccountActions={Boolean(credits.data?.enforcement_enabled)}
        onOpenTasks={() => setTaskOpen(true)}
        onOpenSidebar={() => setSidebarOpen(true)}
        showMenu={showSidebar}
        taskOpen={taskOpen}
        taskTriggerRef={taskTriggerRef}
      />}
      {creationId && <CreationWorkspaceBar key={creationId} featureId={creationId} />}
      <div className="app-shell__body">
        {showSidebar && (
          <ToolSidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
        )}
        <main
          className={[
            "main-content",
            flush ? "main-content--flush" : "",
            !showSidebar ? "main-content--wide" : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          {children}
        </main>
      </div>
      {!pilotAccount && <TaskDrawer
        open={taskOpen}
        onClose={() => setTaskOpen(false)}
        triggerRef={taskTriggerRef}
      />}
      {!pilotAccount && <AnnouncementPrompt />}
    </div>
  );
}

