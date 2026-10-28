"use client";

import { Image as ImageIcon } from "lucide-react";

import { JobResultImage } from "@/components/job-result-image";
import { JobDeliverablesDownload } from "@/components/job-deliverables-download";
import styles from "./product-image-workspace.module.css";

export type DeliverableResult = {
  asset_id: string;
  ordinal: number;
  role: string;
};

export function ResultGrid({
  workspaceId,
  assets,
  expectedCount,
  title = "生成结果",
  archiveJobId,
}: {
  workspaceId?: string;
  assets: DeliverableResult[];
  expectedCount?: number;
  title?: string;
  archiveJobId?: string;
}) {
  if (!assets.length) {
    return (
      <div className={styles.resultEmpty}>
        <span><ImageIcon size={24} /></span>
        <strong>生成结果</strong>
        <p>结果图有效期30天，请尽快保存</p>
      </div>
    );
  }

  return (
    <section className={styles.resultSection} aria-label={title}>
      <div className={styles.sectionHeading}>
        <div>
          <span>04</span>
          <div><h2>{title}</h2><p>每张结果都可独立查看和下载。</p></div>
        </div>
        <strong>{assets.length} / {expectedCount ?? assets.length}</strong>
      </div>
      <div className={styles.resultGrid}>
        {assets.map((asset, index) => (
          <JobResultImage
            key={`${asset.asset_id}-${asset.ordinal}`}
            workspaceId={workspaceId}
            assetId={asset.asset_id}
            alt={`${title} ${index + 1}`}
            label={`IMAGE-${String(asset.ordinal + 1).padStart(3, "0")}`}
          />
        ))}
      </div>
      {archiveJobId ? (
        <JobDeliverablesDownload workspaceId={workspaceId} jobId={archiveJobId} />
      ) : null}
    </section>
  );
}
