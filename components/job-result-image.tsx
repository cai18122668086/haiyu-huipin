"use client";

import { Download, ExternalLink, LoaderCircle } from "lucide-react";
import { usePreviewVisibility } from "@/features/assets/hooks/use-preview-visibility";
import { useState } from "react";
import {
  getAssetAccessUrl,
  useAssetAccessUrl,
} from "@/features/assets/api/assets";
import { errorMessage } from "@/lib/api/request";
import styles from "./workflow-results.module.css";

export function JobResultImage({ workspaceId, assetId, alt, label, width, height }: {
  workspaceId?: string;
  assetId: string;
  alt: string;
  label?: string;
  width?: number;
  height?: number;
}) {
  const { ref, visible } = usePreviewVisibility<HTMLElement>();
  const access = useAssetAccessUrl(workspaceId, visible ? assetId : undefined, "thumbnail");
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string>();
  const [failedUrl, setFailedUrl] = useState<string>();
  const download = async () => {
    if (!workspaceId || isDownloading) return;
    setIsDownloading(true);
    setDownloadError(undefined);
    try {
      const attachment = await getAssetAccessUrl(
        workspaceId,
        assetId,
        "attachment",
      );
      const anchor = document.createElement("a");
      anchor.href = attachment.url;
      anchor.download = "";
      anchor.rel = "noopener";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
    } catch (error) {
      setDownloadError(errorMessage(error));
    } finally {
      setIsDownloading(false);
    }
  };
  if (access.isPending) return <article ref={ref} className={styles.resultState}><LoaderCircle className="spin" />正在获取结果…</article>;
  if (access.error || !access.data?.url) return <article ref={ref} className={`${styles.resultState} ${styles.resultStateError}`} role="alert">{errorMessage(access.error ?? new Error("结果访问地址不可用。"))}</article>;
  if (failedUrl === access.data.url) {
    return (
      <article ref={ref} className={`${styles.resultState} ${styles.resultStateError}`} role="alert">
        <span>结果图片加载失败。</span>
        <button type="button" onClick={() => { setFailedUrl(undefined); void access.refetch(); }}>重新加载</button>
      </article>
    );
  }
  return (
    <article ref={ref} className={styles.resultCard}>
      <a href={access.data.original_url ?? access.data.url} target="_blank" rel="noreferrer" aria-label={`${alt}，单张查看`}><img loading="lazy" decoding="async" src={access.data.url} alt={alt} onError={() => setFailedUrl(access.data?.url)} /><ExternalLink size={15} /></a>
      <div><strong>{label ?? alt}</strong><button type="button" onClick={download} disabled={isDownloading || !workspaceId} aria-label={`下载${alt}`}>{isDownloading ? <LoaderCircle size={14} className="spin" /> : <Download size={14} />}{isDownloading ? "准备中…" : "下载"}</button></div>
      {width && height ? <p className={styles.resultDimensions}>实际尺寸 {width} × {height} px</p> : null}
      {downloadError ? <p className={styles.downloadError} role="alert">{downloadError}</p> : null}
    </article>
  );
}
