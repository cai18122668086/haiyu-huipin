 "use client";

import { FlaskConical, LoaderCircle, ShieldCheck } from "lucide-react";

import {
  presentRuntime,
  useRuntimeStatus,
} from "@/features/runtime/api/runtime";

export function RuntimeCapabilityBanner() {
  const runtime = useRuntimeStatus();
  const presentation = presentRuntime(runtime.data);

  return (
    <div
      className="runtime-capability-banner"
      role="status"
      aria-live="polite"
    >
      <span>
        {runtime.isPending ? (
          <LoaderCircle size={14} className="spin" />
        ) : (
          <FlaskConical size={14} />
        )}
        {presentation.badge}
      </span>
      <p>{presentation.summary}</p>
      <span>
        <ShieldCheck size={14} />
        {presentation.assurance}
      </span>
    </div>
  );
}
