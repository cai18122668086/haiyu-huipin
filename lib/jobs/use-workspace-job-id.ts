"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";

export function useWorkspaceJobId(route: string) {
  const router = useRouter();
  const requestedJobId = useSearchParams().get("jobId")?.trim() || undefined;
  const [local, setLocal] = useState<{ requestedJobId?: string; jobId?: string }>({ requestedJobId });
  // URL navigation takes precedence over a job created in an earlier view.
  // Reset synchronously so switching task links cannot show the previous task.
  if (local.requestedJobId !== requestedJobId) setLocal({ requestedJobId });
  const jobId = requestedJobId ?? (local.requestedJobId === requestedJobId ? local.jobId : undefined);
  const setJobId = (next?: string) => {
    setLocal({ requestedJobId, jobId: next });
    router.replace(next ? `${route}?jobId=${encodeURIComponent(next)}` : route, { scroll: false });
  };
  return [jobId, setJobId] as const;
}

export function useWorkspaceJobReview(jobId?: string) {
  const [reviewedJobId, setReviewedJobId] = useState<string>();
  const setComplete = useCallback((complete: boolean) => setReviewedJobId(complete ? jobId : undefined), [jobId]);
  return [Boolean(jobId && reviewedJobId === jobId), setComplete] as const;
}
