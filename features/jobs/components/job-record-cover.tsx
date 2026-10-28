"use client";

import { AlertTriangle, LoaderCircle } from "lucide-react";
import { useState } from "react";
import { usePreviewVisibility } from "@/features/assets/hooks/use-preview-visibility";
import { useAssetAccessUrl } from "@/features/assets/api/assets";
import type { Job } from "@/lib/api/types";
import { jobTypeLabel } from "@/lib/jobs/presentation";

export function JobRecordCover({ workspaceId, job }: { workspaceId: string; job: Job }) {
  const { ref, visible } = usePreviewVisibility<HTMLDivElement>();
  const access = useAssetAccessUrl(workspaceId, visible ? job.primary_output_asset_id ?? undefined : undefined, "thumbnail");
  const [failedUrl, setFailedUrl] = useState<string>();
  const title = job.display_name?.trim() || jobTypeLabel(job.type);
  const processing = ["queued", "planning", "awaiting_confirmation", "processing", "cancelling"].includes(job.status);

  return (
    <div ref={ref} className="record-cover api-job-cover" aria-label={`${title}生成结果预览`}>
      {access.data?.url && failedUrl !== access.data.url ? (
        <img loading="lazy" decoding="async" src={access.data.url} alt={`${title}生成结果`} onError={() => setFailedUrl(access.data?.url)} />
      ) : access.isFetching || processing ? (
        <LoaderCircle className="spin" size={20} aria-label="正在加载结果预览" />
      ) : job.status === "failed" ? (
        <span className="record-cover__empty record-cover__empty--failed"><AlertTriangle size={15} />生成失败</span>
      ) : (
        <span className="record-cover__empty">暂无结果</span>
      )}
      {job.output_asset_count > 1 ? (
        <span className="record-cover__count">+{job.output_asset_count - 1}</span>
      ) : null}
    </div>
  );
}
