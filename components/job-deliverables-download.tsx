"use client";

import { Archive, LoaderCircle } from "lucide-react";
import { useState } from "react";

import { getJobDeliverablesArchive } from "@/features/jobs/api/jobs";
import { errorMessage } from "@/lib/api/request";
import styles from "./workflow-results.module.css";

export function JobDeliverablesDownload({
  workspaceId,
  jobId,
}: {
  workspaceId?: string;
  jobId: string;
}) {
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string>();

  const download = async () => {
    if (!workspaceId || isDownloading) return;
    setIsDownloading(true);
    setDownloadError(undefined);
    try {
      const archive = await getJobDeliverablesArchive(workspaceId, jobId);
      const url = URL.createObjectURL(archive);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `polaris-${jobId}-deliverables.zip`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      setDownloadError(errorMessage(error));
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <div className={styles.archiveAction}>
      <button
        type="button"
        onClick={download}
        disabled={!workspaceId || isDownloading}
      >
        {isDownloading ? <LoaderCircle size={16} className="spin" /> : <Archive size={16} />}
        {isDownloading ? "正在打包…" : "下载整套 ZIP"}
      </button>
      {downloadError ? <p role="alert">{downloadError}</p> : null}
    </div>
  );
}
