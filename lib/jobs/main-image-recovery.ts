type RecoveryJob = {
  status: string;
  stage?: string | null;
  allowed_actions: readonly string[];
  plan?: {
    confirmed_at?: string | null;
    payload: Record<string, unknown>;
  } | null;
};

export function mainImageRecoveryMode(job?: RecoveryJob): "replan" | "retry" | null {
  if (!job || !["failed", "partial", "cancelled"].includes(job.status)
    || !job.allowed_actions.includes("retry")) return null;
  if (job.plan?.confirmed_at && Array.isArray(job.plan.payload.pages)
    && job.plan.payload.pages.length === 6) return "retry";
  if (!job.plan && job.status === "failed" && job.stage === "planning_failed") return "replan";
  return null;
}
