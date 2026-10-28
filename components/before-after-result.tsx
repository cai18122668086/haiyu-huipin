"use client";

import { Download, LoaderCircle } from "lucide-react";
import { useState } from "react";
import { useAssetAccessUrl } from "@/features/assets/api/assets";
import { errorMessage } from "@/lib/api/request";
import styles from "./workflow-results.module.css";

export function BeforeAfterResult({ workspaceId, sourceUrl, assetId }: {
  workspaceId?: string;
  sourceUrl: string;
  assetId: string;
}) {
  const [position, setPosition] = useState(50);
  const access = useAssetAccessUrl(workspaceId, assetId);
  if (access.isPending) return <div className={styles.resultState}><LoaderCircle className="spin" />正在获取生成结果…</div>;
  if (access.error || !access.data?.url) return <div className={`${styles.resultState} ${styles.resultStateError}`} role="alert">{errorMessage(access.error ?? new Error("结果访问地址不可用。"))}</div>;
  return (
    <div>
      <div className={styles.comparisonStage}>
        <img src={sourceUrl} alt="一句话改图前的原图" />
        <div className={styles.comparisonAfter} style={{ clipPath: `inset(0 0 0 ${position}%)` }}><img src={access.data.url} alt="一句话改图后的生成结果" /></div>
        <span className={`${styles.comparisonLabel} ${styles.beforeLabel}`}>原图</span>
        <span className={`${styles.comparisonLabel} ${styles.afterLabel}`}>结果</span>
        <i className={styles.divider} style={{ left: `${position}%` }} />
      </div>
      <div className={styles.comparisonControls}>
        <label>前后对比<input type="range" min="0" max="100" value={position} onChange={(event) => setPosition(Number(event.target.value))} aria-label="调整原图与结果对比位置" /></label>
        <a href={access.data.url} download><Download size={15} />下载结果</a>
      </div>
    </div>
  );
}
