"use client";

import { CreditCostHint } from "@/features/billing/components/credit-account";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { assetHandoffKey } from "@/lib/assets/creation-handoff";
import { useAssetHandoff } from "@/features/assets/hooks/use-asset-handoff";
import { AlertCircle, Check, CheckCircle2, ImagePlus, Images, LoaderCircle, RotateCcw, ShieldCheck, Sparkles, Upload } from "lucide-react";

import { ImageDropZone } from "@/components/image-drop-zone";
import { JobProgressFeedback } from "@/components/job-progress-feedback";
import { AppShell } from "@/components/app-shell";
import { WorkspaceJobGate } from "@/components/workspace-job-gate";
import { useWorkspaceJobId } from "@/lib/jobs/use-workspace-job-id";
import { workspaceJobSnapshot } from "@/lib/jobs/workspace-restore";
import { AssetLibraryDialog } from "@/components/asset-library-picker";
import { BeforeAfterResult } from "@/components/before-after-result";
import { JobResultImage } from "@/components/job-result-image";
import { MaskEditor, normalizeRasterImage, type MaskEditorHandle } from "@/components/mask-editor";
import { WorkspaceHeader } from "@/components/workspace-header";
import { useAsset, useAssetAccessUrl, useUploadAsset } from "@/features/assets/api/assets";
import { useCreateJob, useJob, useRetryJob } from "@/features/jobs/api/jobs";
import { presentWorkflow, useRuntimeStatus } from "@/features/runtime/api/runtime";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { errorMessage } from "@/lib/api/request";
import { IMAGE_UPLOAD_FORMAT_HINT } from "@/lib/image-upload";
import type { Asset } from "@/lib/api/types";
import { allowsJobAction } from "@/lib/jobs/actions";
import { selectJobResultAssets } from "@/lib/jobs/results";
import workspaceStyles from "@/components/product-image-workspace.module.css";

export default function LocalEditPage() {
  const search = useSearchParams();
  return <LocalEditWorkspace key={assetHandoffKey(search)} />;
}

function LocalEditWorkspace() {
  const fileInput = useRef<HTMLInputElement>(null);
  const maskEditor = useRef<MaskEditorHandle>(null);
  const [draftPrompt, setDraftPrompt] = useState("");
  const [promptEdited, setPromptEdited] = useState(false);
  const setPrompt = (value: string) => { setDraftPrompt(value); setPromptEdited(true); };
  const [sourceOverridden, setSourceOverridden] = useState(false);
  const [draftSourceAsset, setSourceAsset] = useState<Asset>();
  const [previewUrl, setPreviewUrl] = useState<string>();
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [hasSelection, setHasSelection] = useState(false);
  const [editorError, setEditorError] = useState<string>();
  const [preparing, setPreparing] = useState(false);
  const submissionLock = useRef(false);
  const { workspace } = useWorkspace();
  const [jobId, setJobId] = useWorkspaceJobId("/workspace/local-edit");
  const handoff = useAssetHandoff(workspace?.id, 1, jobId);
  const job = useJob(workspace?.id, jobId);
  const saved = workspaceJobSnapshot(job.data);
  const restoredSourceId = saved.id("source_asset_id");
  const restoredMaskId = saved.id("selection_mask_asset_id");
  const restoredSource = useAsset(workspace?.id, jobId ? restoredSourceId : undefined);
  const sourceAsset = jobId ? restoredSource.data : draftSourceAsset ?? (!sourceOverridden ? handoff.data?.sources[0]?.asset : undefined);
  const prompt = jobId ? saved.text("instruction") : promptEdited ? draftPrompt : handoff.data?.instruction ?? draftPrompt;
  const quality = jobId ? saved.choice("quality", ["preview", "balanced", "final", "auto"], "balanced") : "balanced";
  const executionPath = jobId ? saved.choice("execution_path", ["auto", "deterministic", "hybrid", "cropped", "full"], "auto") : "auto";
  const upload = useUploadAsset(workspace?.id);
  const sourceAccess = useAssetAccessUrl(
    workspace?.id,
    !jobId && previewUrl ? undefined : sourceAsset?.id,
  );
  const sourceUrl = jobId ? sourceAccess.data?.url : previewUrl ?? sourceAccess.data?.url;
  const createJob = useCreateJob(workspace?.id);
  const retryJob = useRetryJob(workspace?.id);
  const runtime = useRuntimeStatus();
  const workflow = presentWorkflow(runtime.data?.capabilities.local_edit);
  const workflowAvailable = workflow.mode === "mock" || workflow.mode === "provider";
  const resultAsset = selectJobResultAssets(job.data)[0];
  const rawCandidateAsset = job.data?.assets.find(
    (asset) =>
      asset.direction === "intermediate" && asset.role === "provider_raw",
  );
  const rawReviewSuccess = rawCandidateAsset?.validation.raw_visual_success;
  const rawReviewSummary =
    typeof rawCandidateAsset?.validation.visual_review_summary === "string"
      ? rawCandidateAsset.validation.visual_review_summary
      : undefined;

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  const onMaskChange = useCallback((selected: boolean) => {
    setHasSelection(selected);
    setEditorError(undefined);
  }, []);
  const onMaskError = useCallback((message?: string) => setEditorError(message), []);

  const onSourceFile = async (file?: File) => {
    if (!file) return;
    setSourceOverridden(true);
    setSourceAsset(undefined);
    setHasSelection(false);
    setEditorError(undefined);
    if (jobId) setJobId(undefined);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(undefined);
    try {
      const normalized = await normalizeRasterImage(file);
      setPreviewUrl(URL.createObjectURL(normalized));
      setSourceAsset(await upload.mutateAsync({ file: normalized, mediaKind: "image" }));
    } catch (error) {
      setEditorError(errorMessage(error));
    } finally {
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const submit = async () => {
    if (submissionLock.current || jobId || !workflowAvailable || !sourceAsset || !hasSelection || !prompt.trim() || !workspace) return;
    submissionLock.current = true;
    setPreparing(true);
    setEditorError(undefined);
    try {
      const maskFile = await maskEditor.current?.exportMaskFile();
      if (!maskFile) throw new Error("蒙版编辑器尚未就绪。");
      const mask = await upload.mutateAsync({ file: maskFile, mediaKind: "mask" });
      const created = await createJob.mutateAsync({
        type: "local_edit",
        inspiration_case_id: handoff.data?.caseId,
        intent: "submit",
        display_name: `一句话改图 · ${sourceAsset.original_filename}`,
        input: { source_asset_id: sourceAsset.id, selection_mask_asset_id: mask.id },
        // Legacy contract shape: configuration: { instruction: prompt.trim(), output_format: "png", quality: "balanced" }
        configuration: {
          instruction: prompt.trim(),
          output_format: "png",
          quality,
          execution_path: executionPath,
        },
      });
      setJobId(created.id);
    } catch (error) {
      if (!(upload.error || createJob.error)) setEditorError(errorMessage(error));
    } finally {
      submissionLock.current = false;
      setPreparing(false);
    }
  };

  const retry = async () => {
    if (!job.data || !allowsJobAction(job.data.allowed_actions, "retry")) return;
    try {
      const retried = await retryJob.mutateAsync({
        jobId: job.data.id,
        allowedActions: job.data.allowed_actions,
        scope: "failed",
      });
      setJobId(retried.id);
    } catch (error) {
      setEditorError(errorMessage(error));
    }
  };

  const requestError = handoff.error ?? restoredSource.error ?? sourceAccess.error ?? upload.error ?? createJob.error ?? retryJob.error ?? job.error;
  const busy = preparing || handoff.loading || upload.isPending || createJob.isPending || retryJob.isPending;
  const canRetry = job.data && allowsJobAction(job.data.allowed_actions, "retry");
  const currentStep = jobId ? (resultAsset ? 3 : 2) : !sourceAsset ? 0 : !hasSelection ? 1 : 2;
  const nextAction = !sourceAsset ? "先上传一张商品原图" : !hasSelection ? "在左侧画布涂抹或框选需要修改的区域" : !prompt.trim() ? "写下希望如何修改选区，也可以使用下方示例" : workflow.mode === "mock" ? "已准备好，可保存任务并预览流程" : "已准备好，可以开始生成";


  return (
    <WorkspaceJobGate jobId={jobId} job={job.data} error={job.error} onRetry={() => void job.refetch()} expectedType="local_edit">
    <AppShell flush>
      <div className="workspace-page local-edit-page">
        <div className="workspace-main">
          <WorkspaceHeader
          jobId={jobId}
            eyebrow={jobId ? "商品图工具 · 已保存任务" : workflow.mode === "provider" ? "商品图工具 · 在线生成" : workflow.mode === "mock" ? "商品图工具 · 基础流程" : "商品图工具 · 服务状态确认中"}
            title="一句话改图"
            description={jobId ? "查看本次任务的原图、选区、修改要求和生成结果。" : workflow.mode === "provider" ? "上传图片，圈出想改的地方，一句话描述变化。选区外保持原样。" : workflow.mode === "mock" ? "上传图片、圈选区域、描述变化。当前为流程预览，暂不生成图片。" : "当前无法确认生成服务状态，图片生成入口暂时关闭。"}
            meta={<span className="autosave-state"><ShieldCheck size={15} />{workflow.mode === "provider" ? "生成服务可用" : workflow.mode === "mock" ? "编辑功能可用" : "服务状态确认中"}</span>}
          />
          <div className="workflow-steps">
            {["上传原图", "绘制选区", "创建任务", "查看结果"].map((step, index) => {
              return <div key={step} aria-current={index === currentStep ? "step" : undefined} className={index < currentStep ? "done" : index === currentStep ? "active" : ""}><i>{index < currentStep ? <Check size={14} /> : index + 1}</i><span>{step}</span>{index < 3 && <em />}</div>;
            })}
          </div>

          <div className="local-edit-layout">
            <section className="editor-card">
              {jobId ? <div className="job-asset-grid">
                {restoredSourceId && <JobResultImage workspaceId={workspace?.id} assetId={restoredSourceId} alt="任务原始图片" label="任务原始图片" />}
                {restoredMaskId && <JobResultImage workspaceId={workspace?.id} assetId={restoredMaskId} alt="任务授权蒙版" label="任务授权蒙版 · 白色为授权区域" />}
              </div> : <MaskEditor ref={maskEditor} sourceUrl={sourceUrl} sourceName={sourceAsset?.original_filename} onChange={onMaskChange} onError={onMaskError} />}
            </section>

            <aside className="edit-controls">
              <ImageDropZone as="section" label="局部改图原始图片" className="control-card control-card--source"
                inputRef={fileInput} disabled={!workspace || Boolean(jobId) || busy || createJob.isPending} busy={upload.isPending}
                onFiles={(files) => onSourceFile(files[0])}>
                {({ openFileDialog, disabled: uploadDisabled, busy: uploadingFile }) => <>
                  <div className="control-card__heading"><span>1</span><div><strong>原始图片</strong><p>{sourceAsset ? `${sourceAsset.width} × ${sourceAsset.height} 标准化画布` : IMAGE_UPLOAD_FORMAT_HINT}</p></div></div>
                  <div className={`${workspaceStyles.uploadActions} local-edit-upload-actions`} data-has-library>
                    <button type="button" onClick={openFileDialog} disabled={uploadDisabled}>
                      {uploadingFile ? <LoaderCircle className="spin" size={15} /> : <Upload size={15} />}
                      {sourceAsset ? "重新上传" : "上传图片"}
                    </button>
                    <button type="button" data-library aria-haspopup="dialog" onClick={() => setLibraryOpen(true)} disabled={!workspace || Boolean(jobId) || uploadingFile || createJob.isPending}>
                      <Images size={15} />
                      从素材库选择
                    </button>
                  </div>
                </>}
              </ImageDropZone>
              <section className="control-card">
                <div className="control-card__heading"><span>2</span><div><strong>想怎么修改？</strong><p id="local-edit-prompt-hint">描述选区里的变化，选区外保持原样</p></div></div>
                <div className="textarea-wrap"><textarea disabled={Boolean(jobId) || busy} value={prompt} placeholder="例如：将选区改为深灰色磨砂金属，保留商品结构与光影。" maxLength={500} onChange={(event) => setPrompt(event.target.value)} aria-label="修改要求" aria-describedby="local-edit-prompt-hint" /><span>{prompt.length} / 500</span></div>
                {!jobId && <div className="local-edit-examples" aria-label="修改要求示例">
                  <span>试试这些写法</span>
                  {[
                    ["换颜色", "将选区改为奶油白色，保留原有纹理、商品结构和自然光影。"],
                    ["换材质", "将选区改为深灰色磨砂金属，保留商品轮廓、结构与光影。"],
                    ["去杂物", "移除选区内的杂物，用周围背景自然补齐，保持光线与纹理一致。"],
                  ].map(([label, value]) => <button key={label} type="button" disabled={busy} aria-pressed={prompt === value} onClick={() => setPrompt(value)}>{label}</button>)}
                </div>}
              </section>
              <div className="cost-bar"><span>本次创作</span>{jobId ? <strong>已保存任务 · 输入参数已锁定</strong> : <CreditCostHint jobType="local_edit" count={1} fallback={<strong>{workflow.mode === "provider" ? "在线生成 · 按套餐计费" : workflow.mode === "mock" ? "任务保存 · 暂不生成图片" : "生成服务暂不可用"}</strong>} />}</div>
              {(requestError || editorError) && <div className="generation-feedback error" role="alert"><span><AlertCircle size={16} /></span><div><strong>请求未完成</strong><p>{editorError ?? errorMessage(requestError ?? new Error("未知错误"))}</p></div></div>}
              <JobProgressFeedback job={job.data} pending={createJob.isPending} error={job.error}
                lastSyncedAt={job.dataUpdatedAt} refreshing={job.isFetching} onRefresh={jobId ? () => void job.refetch() : undefined} />
              {rawCandidateAsset && (
                <div className={`generation-feedback ${rawReviewSuccess === true ? "success" : rawReviewSuccess === false ? "error" : "active"}`}>
                  <span>{rawReviewSuccess === true ? <CheckCircle2 size={16} /> : <ShieldCheck size={16} />}</span>
                  <div>
                    <strong>{rawReviewSuccess === true ? "视觉验收通过" : rawReviewSuccess === false ? "视觉验收未通过" : "视觉验收未完成"}</strong>
                    <p>{rawReviewSummary ?? "原始候选已保留；只有验收通过的严格合成结果才可交付。"}</p>
                  </div>
                </div>
              )}
              {canRetry ? (
                <button className="primary-button generate-button" onClick={() => void retry()} disabled={retryJob.isPending}>{retryJob.isPending ? <LoaderCircle className="spin" size={17} /> : <RotateCcw size={17} />}重试失败项目</button>
              ) : (
                <button className="primary-button generate-button" onClick={() => void submit()} disabled={Boolean(jobId) || !workflowAvailable || !sourceAsset || !hasSelection || !prompt.trim() || busy} aria-busy={busy}>
                  {busy ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}{jobId ? "当前为已保存任务" : busy ? "正在创建任务" : workflow.mode === "provider" ? "开始生成" : workflow.mode === "mock" ? "保存任务" : "服务状态确认中，暂不可创建"}
                </button>
              )}
              {!jobId && <p className="local-edit-next-action" role="status">{busy ? "正在准备图片与选区，请稍候…" : !workflowAvailable ? "生成服务暂不可用，请稍后重试" : nextAction}</p>}
              <p className="privacy-line"><ShieldCheck size={13} />只修改紫色选区，选区外保持原样。</p>
            </aside>
          </div>

          {resultAsset && sourceUrl && <section className="result-preview-card"><div className="planning-head"><div><span className="number-chip number-chip--violet">4</span><div><h2>生成结果</h2><p>拖动滑块对比原图与生成结果。</p></div></div></div><BeforeAfterResult workspaceId={workspace?.id} sourceUrl={sourceUrl} assetId={resultAsset.asset_id} /></section>}
          {rawCandidateAsset && <section className="detail-form-card"><div className="form-section-head"><span className="number-chip number-chip--violet">R</span><div><h2>原始生成候选</h2><p>仅供视觉诊断，不是像素锁定后的可交付结果。</p></div></div><div className="job-results-grid"><JobResultImage workspaceId={workspace?.id} assetId={rawCandidateAsset.asset_id} alt="一句话改图原始候选" label="原始候选 · 不可交付" /></div></section>}
          {job.data?.status === "completed" && workflow.mode === "mock" && !resultAsset && <div className="api-state"><ImagePlus size={22} /><p>任务流程已完成，当前生成服务未返回图片结果。</p></div>}
        </div>
        {libraryOpen ? (
          <AssetLibraryDialog
            workspaceId={workspace?.id}
            title="选择原始图片"
            description="选择需要进行一句话改图的图片。"
            selectedIds={sourceAsset ? [sourceAsset.id] : []}
            maxItems={1}
            disabled={upload.isPending}
            onClose={() => setLibraryOpen(false)}
            onConfirm={(_, assets) => {
              const selectedAsset = assets[0];
              if (!selectedAsset) return;
              setSourceAsset(selectedAsset);
              setSourceOverridden(true);
              setHasSelection(false);
              setEditorError(undefined);
              if (jobId) setJobId(undefined);
              if (previewUrl) URL.revokeObjectURL(previewUrl);
              setPreviewUrl(undefined);
              setLibraryOpen(false);
            }}
          />
        ) : null}
      </div>
    </AppShell>
    </WorkspaceJobGate>
  );
}
