"use client";

import { usePreviewVisibility } from "@/features/assets/hooks/use-preview-visibility";

import { Check, FileWarning, Image as ImageIcon, LoaderCircle, X } from "lucide-react";
import { useEffect, useState } from "react";

import { useAssetAccessUrl, useAssets } from "@/features/assets/api/assets";
import type { Asset } from "@/lib/api/types";
import { useBodyScrollLock } from "@/lib/hooks/use-body-scroll-lock";
import styles from "./product-image-workspace.module.css";

function LibraryAssetCard({
  asset,
  workspaceId,
  selected,
  disabled,
  onToggle,
}: {
  asset: Asset;
  workspaceId: string;
  selected: boolean;
  disabled: boolean;
  onToggle: () => void;
}) {
  const { ref: previewRef, visible: previewVisible } = usePreviewVisibility<HTMLButtonElement>();
  const access = useAssetAccessUrl(workspaceId, previewVisible ? asset.id : undefined, "thumbnail");
  const dimensions = asset.width && asset.height
    ? `${asset.width} × ${asset.height}`
    : "尺寸待识别";

  return (
    <button
      ref={previewRef}
      type="button"
      className={styles.libraryAsset}
      data-selected={selected || undefined}
      disabled={disabled}
      onClick={onToggle}
      aria-pressed={selected}
      aria-label={`${selected ? "取消选择" : "选择"} ${asset.original_filename}`}
    >
      <span className={styles.libraryAssetPreview}>
        {access.data?.url ? (
          <img src={access.data.url} alt="" loading="lazy" decoding="async" />
        ) : access.isError ? (
          <FileWarning size={22} />
        ) : (
          <LoaderCircle className="spin" size={22} />
        )}
        <i>{selected ? <Check size={14} /> : null}</i>
      </span>
      <span className={styles.libraryAssetCopy}>
        <strong>{asset.original_filename}</strong>
        <small>{dimensions}</small>
      </span>
    </button>
  );
}

export function AssetLibraryPicker({
  workspaceId,
  title,
  description,
  selectedIds,
  excludedIds = [],
  onChange,
  maxItems = 8,
  disabled = false,
}: {
  workspaceId?: string;
  title: string;
  description: string;
  selectedIds: string[];
  excludedIds?: string[];
  onChange: (assetIds: string[]) => void;
  maxItems?: number;
  disabled?: boolean;
}) {
  const assetsQuery = useAssets(workspaceId, { limit: 60 });
  const assets = (assetsQuery.data?.items ?? []).filter(
    (asset) => asset.lifecycle_status === "ready" && asset.media_kind === "image",
  );

  const toggle = (assetId: string) => {
    if (selectedIds.includes(assetId)) {
      onChange(selectedIds.filter((id) => id !== assetId));
      return;
    }
    if (selectedIds.length < maxItems) onChange([...selectedIds, assetId]);
  };

  return (
    <section className={styles.assetPicker} aria-label={title}>
      <div className={styles.assetPickerHeading}>
        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>
        <span>{selectedIds.length} / {maxItems}</span>
      </div>

      {!workspaceId || assetsQuery.isPending ? (
        <div className={styles.libraryState}>
          <LoaderCircle className="spin" size={19} /> 正在读取素材库…
        </div>
      ) : assetsQuery.isError ? (
        <div className={styles.libraryState} role="alert">
          <FileWarning size={19} /> 素材库暂时不可用，请先使用本地上传。
        </div>
      ) : assets.length ? (
        <div className={styles.libraryGrid}>
          {assets.map((asset) => (
            <LibraryAssetCard
              key={asset.id}
              asset={asset}
              workspaceId={workspaceId}
              selected={selectedIds.includes(asset.id)}
              disabled={
                disabled ||
                excludedIds.includes(asset.id) ||
                (!selectedIds.includes(asset.id) && selectedIds.length >= maxItems)
              }
              onToggle={() => toggle(asset.id)}
            />
          ))}
        </div>
      ) : (
        <div className={styles.libraryState}>
          <ImageIcon size={19} /> 素材库暂无图片，上传后会自动出现在这里。
        </div>
      )}
    </section>
  );
}

export function AssetLibraryDialog({
  workspaceId,
  title,
  description,
  selectedIds,
  excludedIds = [],
  onConfirm,
  onClose,
  maxItems = 8,
  disabled = false,
}: {
  workspaceId?: string;
  title: string;
  description: string;
  selectedIds: string[];
  excludedIds?: string[];
  onConfirm: (assetIds: string[], assets: Asset[]) => void;
  onClose: () => void;
  maxItems?: number;
  disabled?: boolean;
}) {
  const [draftIds, setDraftIds] = useState(selectedIds);
  const assetsQuery = useAssets(workspaceId, { limit: 60 });

  useBodyScrollLock(true);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  return (
    <div className={styles.libraryDialogBackdrop} onMouseDown={onClose}>
      <section
        className={styles.libraryDialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="asset-library-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className={styles.libraryDialogHeader}>
          <div>
            <h2 id="asset-library-dialog-title">{title}</h2>
            <p>{description}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭素材库">
            <X size={20} />
          </button>
        </header>

        <div className={styles.libraryDialogBody}>
          <AssetLibraryPicker
            workspaceId={workspaceId}
            title="个人素材库"
            description={`选择需要使用的图片，最多 ${maxItems} 张。`}
            selectedIds={draftIds}
            excludedIds={excludedIds}
            onChange={setDraftIds}
            maxItems={maxItems}
            disabled={disabled}
          />
        </div>

        <footer className={styles.libraryDialogFooter}>
          <span>已选择 {draftIds.length} / {maxItems} 张</span>
          <div>
            <button type="button" onClick={onClose}>取消</button>
            <button
              type="button"
              data-primary
              disabled={disabled}
              onClick={() => onConfirm(
                draftIds,
                draftIds.flatMap((assetId) => {
                  const asset = assetsQuery.data?.items.find((item) => item.id === assetId);
                  return asset ? [asset] : [];
                }),
              )}
            >
              确定
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}
