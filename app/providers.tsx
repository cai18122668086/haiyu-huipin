"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { AuthBoundary } from "@/components/auth/auth-boundary";
import { initializeAuthClient } from "@/lib/auth/client";
import { getAuthMode } from "@/lib/auth/config";
import { createQueryClient } from "@/lib/query/query-client";

export function Providers({ children }: { children: React.ReactNode }) {
  if (getAuthMode() !== "development") initializeAuthClient();
  const [queryClient] = useState(createQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <AuthBoundary>{children}</AuthBoundary>
    </QueryClientProvider>
  );
}
