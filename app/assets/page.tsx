"use client";

import { usePreviewVisibility } from "@/features/assets/hooks/use-preview-visibility";

import { useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowUpRight,
  Check,
  CloudUpload,
  FileWarning,
  FolderOpen,
  Image as ImageIcon,
  LayoutGrid,
  LoaderCircle,
  Paintbrush2,
  Search,
  Sparkles,
  Upload,
} from "lucide-react";

import { ImageDropZone } from "@/components/image-drop-zone";
import dropStyles from "@/components/image-drop-zone.module.css";
import { AppShell } from "@/components/app-shell";
import {
  useAssetAccessUrl,
  useAssets,
  useUploadAsset,
} from "@/features/assets/api/assets";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { errorMessage } from "@/lib/api/request";
import type { Asset } from "@/lib/api/types";
import { formatDate } from "@/lib/jobs/presentation";
import { IMAGE_UPLOAD_FORMAT_HINT } from "@/lib/image-upload";
import { assetCreationHref } from "@/lib/assets/creation-handoff";

function AssetCard({
  asset,
  workspaceId,
  selected,
  onToggle,
}: {
  asset: Asset;
  workspaceId: string;
  selected: boolean;
  onToggle: () => void;
}) {
  const { ref: previewRef, visible: previewVisible } = usePreviewVisibility<HTMLElement>();
  const accessUrl = useAssetAccessUrl(workspaceId, previewVisible ? asset.id : undefined, "thumbnail");
  return (
    <article ref={previewRef} className={`asset-card ${selected ? "selected" : ""}`}>
      <div className="asset-image tone-violet">
        {accessUrl.data ? (
          <img src={accessUrl.data.url} alt={asset.original_filename} loading="lazy" decoding="async" />
        ) : (
          <div className="api-image-placeholder" aria-label="正在加载素材预览">
            {accessUrl.isError ? <FileWarning size={26} /> : <LoaderCircle className="spin" size={26} />}
          </div>
        )}
        <button className="asset-check" onClick={onToggle} aria-label={`选择 ${asset.original_filename}`}>
          {selected && <Check size={14} />}
        </button>
        <span>{{ upload: "上传原图", generated: "任务产物", imported: "导入素材", derived: "派生素材" }[asset.origin_kind]}</span>
      </div>
      <div className="asset-info">
        <div>
          <strong>{asset.original_filename}</strong>
          {accessUrl.data && (
            <a href={accessUrl.data.original_url ?? accessUrl.data.url} target="_blank" rel="noreferrer" aria-label="打开素材">
              <ArrowUpRight size={15} />
            </a>
          )}
        </div>
        <p>{asset.content_type} · {formatDate(asset.created_at)}</p>
        <span>
          {asset.width && asset.height ? `${asset.width} × ${asset.height}` : "尺寸待识别"}
          {" · "}
          {(asset.byte_size / 1024 / 1024).toFixed(1)} MB
        </span>
        {asset.expires_at && <span>保留至 {formatDate(asset.expires_at)}</span>}
      </div>
    </article>
  );
}

export default function AssetsPage() {
  const router = useRouter();
  const fileInput = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const { workspace, isPending: workspacePending, error: workspaceError } = useWorkspace();
  const assetsQuery = useAssets(workspace?.id);
  const upload = useUploadAsset(workspace?.id);
  const assets = useMemo(
    () =>
      (assetsQuery.data?.items ?? []).filter((asset) =>
        asset.original_filename.toLowerCase().includes(query.toLowerCase()),
      ),
    [assetsQuery.data, query],
  );
  const requestError = workspaceError ?? assetsQuery.error ?? upload.error;

  const [batchStatus, setBatchStatus] = useState("");
  const [failedFiles, setFailedFiles] = useState<File[]>([]);
  const batchLock = useRef(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const chooseFile = () => fileInput.current?.click();
  const onFiles = async (files: File[]) => {
    if (batchLock.current || !files.length) return;
    batchLock.current = true; setBatchBusy(true);
    const failed: File[] = [];
    let completed = 0;
    try {
      for (const file of files) {
        try { await upload.mutateAsync({ file }); completed += 1; } catch { failed.push(file); }
        setBatchStatus(`已上传 ${completed} / ${files.length} 张${failed.length ? `，${failed.length} 张未完成` : ""}`);
      }
      setFailedFiles(failed);
    } finally { batchLock.current = false; setBatchBusy(false); }
  };
  const toggleSelected = (id: string) =>
    setSelected((items) => (items.includes(id) ? items.filter((item) => item !== id) : [...items, id]));

  return (
    <AppShell>
      <div className="content-page assets-page">
        <div className="page-title-row">
          <div>
            <span>素材管理</span>
            <h1>个人素材库</h1>
            <p>上传素材和生成图片保留 30 天，到期自动删除且无法恢复，请及时下载。</p>
          </div>
          <button className="primary-button" onClick={chooseFile} disabled={!workspace || upload.isPending}>
            {upload.isPending ? <LoaderCircle className="spin" size={17} /> : <Upload size={17} />}
            {upload.isPending ? "正在上传" : "上传素材"}
          </button>
        </div>

        <section className="asset-overview">
          <div className="storage-card">
            <span><FolderOpen size={21} /></span>
            <div><p>当前工作区</p><strong>{workspace?.name ?? "正在连接"}</strong></div>
            <em>素材中心</em>
          </div>
          <div><ImageIcon size={19} /><span><p>已加载素材</p><strong>{assetsQuery.data?.total ?? assets.length}</strong></span></div>
          <div><Sparkles size={19} /><span><p>任务产物</p><strong>{assets.filter((item) => item.origin_kind === "generated").length}</strong></span></div>
          <div><CloudUpload size={19} /><span><p>上传原图</p><strong>{assets.filter((item) => item.origin_kind === "upload").length}</strong></span></div>
        </section>

        <section className="asset-library" aria-busy={workspacePending || assetsQuery.isPending}>
          <div className="asset-library__head">
            <div className="asset-tabs"><button className="active">全部素材 <span>{assets.length}</span></button></div>
            <div className="view-toggle"><button className="active" aria-label="网格视图"><LayoutGrid size={17} /></button></div>
          </div>
          <div className="asset-toolbar">
            <label className="search-field" htmlFor="asset-search">
              <span className="visually-hidden">搜索素材</span>
              <Search size={16} />
              <input id="asset-search" placeholder="搜索素材名称" value={query} onChange={(event) => setQuery(event.target.value)} />
            </label>
            <span className="asset-count">{assets.length} 个素材</span>
          </div>

          {requestError && (
            <div className="api-state api-state--error" role="alert">
              <FileWarning size={24} />
              <div><strong>素材服务暂时不可用</strong><p>{errorMessage(requestError)}</p></div>
              <button className="secondary-button" onClick={() => void assetsQuery.refetch()}>重新连接</button>
            </div>
          )}
          {(workspacePending || assetsQuery.isPending) && (
            <div className="api-state"><LoaderCircle className="spin" size={24} /><p>正在加载素材…</p></div>
          )}
          {!requestError && !assetsQuery.isPending && assets.length === 0 && (
            <div className="empty-state">
              <Upload size={28} /><h3>{query ? "没有匹配素材" : "还没有素材"}</h3>
              <p>{query ? "清除搜索后再试。" : "上传一张图片即可开始管理和创作素材。"}</p>
              <button className="secondary-button" onClick={query ? () => setQuery("") : chooseFile}>{query ? "清除搜索" : "上传第一张素材"}</button>
            </div>
          )}
          {selected.length > 0 && (
            <div className="selection-toolbar">
              <span><i><Check size={13} /></i>已选择 {selected.length} 项</span>
              <button disabled={!workspace || selected.length !== 1} title="选择 1 张图片进行局部修改" onClick={() => router.push(assetCreationHref("/workspace/local-edit", workspace!.id, selected))}><Paintbrush2 size={15} />带入一句话改图</button>
              <button disabled={!workspace || selected.length > 8} title="选择 1–8 张商品图片" onClick={() => router.push(assetCreationHref("/workspace/detail-pack", workspace!.id, selected))}><Sparkles size={15} />创建商品详情图</button>
              <button disabled={!workspace || selected.length > 8} onClick={() => router.push(assetCreationHref("/workspace/domestic-main-image", workspace!.id, selected))}>制作主图套图</button><button disabled={!workspace || selected.length > 6} onClick={() => router.push(assetCreationHref("/style-replica", workspace!.id, selected))}>开始一键复刻</button><button onClick={() => setSelected([])}>取消选择</button>
            </div>
          )}
          {batchStatus && <p role="status">{batchStatus}</p>}{failedFiles.length > 0 && <button className="secondary-button" disabled={batchBusy} onClick={() => void onFiles(failedFiles)}>仅重试未完成的 {failedFiles.length} 张</button>}
          <div className="asset-grid" data-empty={assets.length === 0 || undefined}>
            <ImageDropZone label="素材中心" className={dropStyles.assetTile} inputRef={fileInput}
              multiple maxFiles={20} disabled={!workspace || batchBusy} busy={batchBusy} showHint={false} onFiles={files => void onFiles(files)}>
              {({ openFileDialog, disabled: uploadDisabled, busy: uploadingFile }) => <button type="button" className="upload-asset-card" onClick={openFileDialog} disabled={uploadDisabled}>
                <span>{uploadingFile ? <LoaderCircle className="spin" size={24} /> : <Upload size={24} />}</span><strong>{uploadingFile ? "正在上传" : "上传新素材"}</strong>
                <p>点击选择，或将图片拖到此处</p><small>{IMAGE_UPLOAD_FORMAT_HINT} · 每批最多 20 张</small>
              </button>}
            </ImageDropZone>
            {assets.map((asset) => (
              <AssetCard key={asset.id} asset={asset} workspaceId={workspace!.id} selected={selected.includes(asset.id)} onToggle={() => toggleSelected(asset.id)} />
            ))}
          </div>
        </section>
      </div>
    </AppShell>
  );
}
