import type { JobDetail } from "@/lib/api/types";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function workspaceJobSnapshot(job?: Pick<JobDetail, "configuration">) {
  const settings = record(job?.configuration.settings);
  const input = record(job?.configuration.input);
  return {
    text: (key: string, fallback = "") => typeof settings[key] === "string" ? settings[key] as string : fallback,
    lines: (key: string) => Array.isArray(settings[key]) ? settings[key].filter((value): value is string => typeof value === "string").join("\n") : "",
    number: (key: string, fallback: number) => typeof settings[key] === "number" && Number.isFinite(settings[key]) ? settings[key] as number : fallback,
    choice: <T extends string>(key: string, options: readonly T[], fallback: T): T => options.find((value) => value === settings[key]) ?? fallback,
    ids: (key: string): string[] => Array.isArray(input[key]) ? input[key].filter((value): value is string => typeof value === "string" && Boolean(value)) : [],
    id: (key: string) => typeof input[key] === "string" && input[key] ? input[key] as string : undefined,
  };
}

export function matchesWorkspaceJob(job: Pick<JobDetail, "type" | "task_preset" | "configuration">, expectedType: string, expectedPreset?: string) {
  if (job.type !== expectedType) return false;
  if (!expectedPreset) return true;
  return (job.task_preset || workspaceJobSnapshot(job).text("task_type")) === expectedPreset;
}
