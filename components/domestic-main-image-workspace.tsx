"use client";
import { splitProductFacts } from "@/lib/product-facts";

import { useDraftState } from "@/lib/hooks/use-creation-draft";
import { ProductFactsGuidance } from "@/components/product-facts-guidance";

import { CreditCostHint } from "@/features/billing/components/credit-account";

import { AlertTriangle, ArrowRight, ImagePlus, LoaderCircle, ShieldCheck, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { GenerationRecommendation } from "@/components/generation-recommendation";
import { AppShell } from "@/components/app-shell";
import { JobDeliverablesDownload } from "@/components/job-deliverables-download";
import { JobResultImage } from "@/components/job-result-image";
import { StatusBadge } from "@/components/status-badge";
import { useAssetHandoff } from "@/features/assets/hooks/use-asset-handoff";
import { AssetLibraryDialog } from "@/components/asset-library-picker";
import { WorkspaceHeader } from "@/components/workspace-header";
import { FormDisclosure } from "@/components/form-disclosure";
import { usePreviewVisibility } from "@/features/assets/hooks/use-preview-visibility";
import { useAssetAccessUrl, useUploadAsset } from "@/features/assets/api/assets";
import { useConfirmPlan, useCreateJob, useFinalizeJob, useJob, useRetryJob } from "@/features/jobs/api/jobs";
import { presentWorkflow, useRuntimeStatus } from "@/features/runtime/api/runtime";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import type { Asset, DomesticMainImageJobCreate } from "@/lib/api/types";
import { MAIN_IMAGE_SPEED_OPTIONS, mainImageSpeed, mainImageSpeedSettings, useMainImageSpeedDraft } from "@/lib/domestic-main-image-speed";
import { errorMessage } from "@/lib/api/request";
import { IMAGE_UPLOAD_ACCEPT, IMAGE_UPLOAD_EXTENSIONS, IMAGE_UPLOAD_FORMAT_HINT, IMAGE_UPLOAD_MIME_TYPES } from "@/lib/image-upload";
import { allowsJobAction } from "@/lib/jobs/actions";
import { mainImageRecoveryMode } from "@/lib/jobs/main-image-recovery";
import { selectJobResultAssets } from "@/lib/jobs/results";
import styles from "./domestic-main-image-workspace.module.css";

type AuroraShadowMode = "off" | "contract_first_rebuild_v1" | "execution_semantics_v1";
type Source = { asset: Asset; previewUrl: string; fileKey: string };
type PlanPage = {
  page_index: number;
  page_role: string;
  main_selling_point: string;
  supporting_selling_points: string[];
  visual_proof: string;
  composition_and_subject: string;
  reading_path_and_type_hierarchy: string;
  non_repetition_reason: string;
  commercial_closure: string;
  buyer_question?: string;
};

type CampaignStyle = {
  palette_roles?: Record<string, string>;
  typography?: Record<string, string>;
  graphic_language?: string;
  information_surface_geometry?: string;
  photography_finish?: string;
  light_direction?: string;
  color_temperature?: string;
  density_band?: string;
};

type IdentityLock = {
  product_form?: string;
  identity_summary?: string;
  must_preserve?: string[];
  must_not_change?: string[];
};

type FinalReview = {
  set_audit_passed: boolean;
  residual_issues: { page_index?: number; decision_summary: string }[];
};

const roleNames: Record<string, string> = {
  SEARCH_MAIN_IMAGE_LISTING_COVER: "搜索主图",
  CAROUSEL_IDENTITY_HERO: "身份英雄图",
  CAROUSEL_CORE_FEATURE_PROOF: "核心卖点证据",
  CAROUSEL_SCENE_OR_OPERATION: "场景 / 操作",
  CAROUSEL_DETAIL_OR_TRUST: "细节 / 信任",
  CAROUSEL_OFFER_OR_SPEC_CLOSURE: "权益 / 规格收口",
};

const MAX_SOURCE_IMAGES = 8;

function fileKey(file: File) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function isSupportedImage(file: File) {
  const lowerName = file.name.toLowerCase();
  return IMAGE_UPLOAD_MIME_TYPES.some((mimeType) => mimeType === file.type)
    || IMAGE_UPLOAD_EXTENSIONS.some((extension) => lowerName.endsWith(extension));
}

function generationItemLabel(status: string, resultSource?: string | null, fast = false) {
  // A retry child job carries pages that already passed in the first job as
  // `reused`. They are successful plan items, not pages waiting to run.
  if (resultSource === "generated" || resultSource === "reused" || status === "reused") return "已生成";
  if (status === "running") return fast ? "生成中" : "生成 / 审图中";
  if (status === "failed") return "生成已失败";
  if (status === "skipped") return "被前页阻断";
  if (status === "succeeded") return "已完成但无图片";
  return "等待生成";
}

function domesticFailureMessage(message?: string | null) {
  if (!message) return "生成流程未完成，请查看失败原因后重试。";
  const sizeMatch = message.match(/must be square 1:1, got (\d+)x(\d+)/i);
  if (sizeMatch) {
    return `图片服务返回了 ${sizeMatch[1]}×${sizeMatch[2]} 非方图，未满足 1:1 主图要求。`;
  }
  return message;
}

function lines(value: string, limit: number) { return splitProductFacts(value, limit); }

function readPlan(payload?: Record<string, unknown>): PlanPage[] {
  if (!payload || !Array.isArray(payload.pages)) return [];
  return payload.pages.filter((page): page is PlanPage => Boolean(page && typeof page === "object"));
}

function recordValue<T>(payload: Record<string, unknown> | undefined, key: string): T | undefined {
  const value = payload?.[key];
  return value && typeof value === "object" && !Array.isArray(value) ? value as T : undefined;
}

function styleSummary(value?: Record<string, string>) {
  return value ? Object.values(value).filter(Boolean).join("；") : "由当前商品身份颜色与用户偏好共同推导";
}

function RestoredSource({ workspaceId, assetId, index }: { workspaceId?: string; assetId: string; index: number }) {
  const { ref, visible } = usePreviewVisibility<HTMLElement>();
  const access = useAssetAccessUrl(workspaceId, visible ? assetId : undefined, "thumbnail");
  return <figure ref={ref}>{access.data?.url ? <img loading="lazy" decoding="async" src={access.data.url} alt={`商品素材 ${index + 1}`} /> : <LoaderCircle className="spin" size={20} />}<figcaption>证据 {String(index + 1).padStart(2, "0")}</figcaption></figure>;
}

export function DomesticMainImageWorkspace() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedJobId = searchParams.get("jobId")?.trim() || undefined;
  const fileInput = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const sourcesRef = useRef<Source[]>([]);
  const { workspace } = useWorkspace();
  const handoff = useAssetHandoff(workspace?.id, 8, requestedJobId);
  const handoffApplied = useRef("");
  const upload = useUploadAsset(workspace?.id);
  const createJob = useCreateJob(workspace?.id);
  const confirmPlan = useConfirmPlan(workspace?.id);
  const finalizeJob = useFinalizeJob(workspace?.id);
  const retryJob = useRetryJob(workspace?.id);
  const runtime = useRuntimeStatus();
  const workflow = presentWorkflow(
    runtime.data?.capabilities.features?.domestic_main_image,
  );
  const [sources, setSources] = useDraftState<Source[]>(workspace?.id, "domestic-main-image", "sources", []);
  const [libraryOpen, setLibraryOpen] = useState(false);
  useEffect(() => {
    if (!handoff.data || handoffApplied.current === handoff.key) return;
    handoffApplied.current = handoff.key;
    setSources(handoff.data.sources.map(source => ({ asset: source.asset, previewUrl: source.previewUrl, fileKey: source.asset.id })));
  }, [handoff.data, handoff.key, setSources]);
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const [productName, setProductName] = useDraftState(workspace?.id, "domestic-main-image", "productName", "");
  const [category, setCategory] = useDraftState(workspace?.id, "domestic-main-image", "category", "");
  const [sellingPoints, setSellingPoints] = useDraftState(workspace?.id, "domestic-main-image", "sellingPoints", "");
  const [displayFacts, setDisplayFacts] = useDraftState(workspace?.id, "domestic-main-image", "displayFacts", "");
  const [scenes, setScenes] = useDraftState(workspace?.id, "domestic-main-image", "scenes", "");
  const [offers, setOffers] = useDraftState(workspace?.id, "domestic-main-image", "offers", "");
  const [trustFacts, setTrustFacts] = useDraftState(workspace?.id, "domestic-main-image", "trustFacts", "");
  const [style, setStyle] = useDraftState(workspace?.id, "domestic-main-image", "style", "");
  const [instruction, setInstruction] = useDraftState(workspace?.id, "domestic-main-image", "instruction", "");
  const [speed, setSpeed] = useMainImageSpeedDraft(workspace?.id);
  const [shadowMode, setShadowMode] = useState<AuroraShadowMode>("off");
  const [createdJobId, setCreatedJobId] = useState<string>();
  const jobId = requestedJobId ?? createdJobId;
  const [localError, setLocalError] = useState<string>();
  const job = useJob(workspace?.id, jobId);
  const automaticPlan = !jobId || (job.data?.configuration.settings as Record<string, unknown> | undefined)?.auto_start_after_plan === true;
  const planPayload = job.data?.plan?.payload;
  const plan = readPlan(planPayload);
  const campaignStyle = recordValue<CampaignStyle>(planPayload, "campaign_style");
  const identityLock = recordValue<IdentityLock>(planPayload, "product_identity_lock");
  const finalReview = recordValue<FinalReview>(planPayload, "final_review");
  const restoredAssetIds = job.data?.assets.filter((asset) => asset.direction === "input" && asset.role === "product_identity_authority").sort((left, right) => left.ordinal - right.ordinal).map((asset) => asset.asset_id) ?? [];
  const restoredSettings = job.data?.configuration.settings && typeof job.data.configuration.settings === "object" ? job.data.configuration.settings as Record<string, unknown> : undefined;
  const restoredString = (key: string) => typeof restoredSettings?.[key] === "string" ? restoredSettings[key] as string : "";
  const restoredLines = (key: string) => Array.isArray(restoredSettings?.[key]) ? (restoredSettings[key] as unknown[]).map(String).join("\n") : "";
  const restoredShadowMode = typeof restoredSettings?.aurora_shadow_mode === "string"
    ? restoredSettings.aurora_shadow_mode as AuroraShadowMode
    : "off";
  const activeShadowMode = jobId ? restoredShadowMode : shadowMode;
  const activeSpeed = jobId ? mainImageSpeed(restoredSettings?.main_image_speed) : speed;
  const fastMode = activeSpeed !== "standard";
  const results = selectJobResultAssets(job.data);
  const acceptedResultCount = job.data
    ? results.filter((asset) => job.data?.items.some(
      (item) => item.ordinal === asset.ordinal && ["succeeded", "reused"].includes(item.status),
    )).length
    : 0;
  const providerReady = workflow.mode === "provider";
  const failedItem = job.data?.items.find((item) => item.status === "failed");
  const setAuditFailed = failedItem?.error_code === "AURORA_SET_AUDIT_FAILED";
  const hasCompleteSet = Boolean(
    ["awaiting_final_review", "completed"].includes(job.data?.status ?? "")
    && results.length === 6
    && (acceptedResultCount === 6 || setAuditFailed),
  );
  const generatedResultCount = results.length;
  const hasAllGeneratedImages = generatedResultCount === 6;
  const blockedItemCount = job.data?.items.filter((item) => item.status === "skipped").length ?? 0;
  const terminalIncomplete = Boolean(
    job.data
    && ["partial", "failed", "cancelled"].includes(job.data.status)
    && !hasCompleteSet,
  );
  const recoveryMode = mainImageRecoveryMode(job.data);
  const showGeneration = Boolean(
    job.data && ["processing", "awaiting_final_review", "completed", "partial", "failed"].includes(job.data.status),
  );
  const busy = upload.isPending || createJob.isPending || confirmPlan.isPending || finalizeJob.isPending || retryJob.isPending;
  const canCreate = Boolean(
    !jobId && workspace && sources.length && productName.trim() && category.trim() && lines(sellingPoints, 24).length && workflow.canSubmit,
  );

  useEffect(() => {
    sourcesRef.current = sources;
  }, [sources]);

  useEffect(() => () => {
    sourcesRef.current.forEach((source) => URL.revokeObjectURL(source.previewUrl));
  }, []);

  async function addFiles(files: File[]) {
    setLocalError(undefined);
    const existingKeys = new Set(sources.map((source) => source.fileKey));
    const uniqueFiles = files.filter((file, index, batch) => (
      isSupportedImage(file)
      && !existingKeys.has(fileKey(file))
      && batch.findIndex((candidate) => fileKey(candidate) === fileKey(file)) === index
    ));
    const availableSlots = MAX_SOURCE_IMAGES - sources.length;
    const acceptedFiles = uniqueFiles.slice(0, availableSlots);
    const unsupportedCount = files.filter((file) => !isSupportedImage(file)).length;
    const duplicateCount = files.length - unsupportedCount - uniqueFiles.length;
    const overflowCount = Math.max(0, uniqueFiles.length - availableSlots);

    if (!acceptedFiles.length) {
      if (availableSlots <= 0) setLocalError(`最多只能上传 ${MAX_SOURCE_IMAGES} 张商品原图。`);
      else if (unsupportedCount) setLocalError(`${IMAGE_UPLOAD_FORMAT_HINT}。`);
      else if (duplicateCount) setLocalError("这些照片已经添加过了，无需重复上传。");
      return;
    }

    if (unsupportedCount || duplicateCount || overflowCount) {
      const skipped = [
        unsupportedCount ? `${unsupportedCount} 张格式不支持` : "",
        duplicateCount ? `${duplicateCount} 张重复照片` : "",
        overflowCount ? `${overflowCount} 张超过数量上限` : "",
      ].filter(Boolean).join("、");
      setLocalError(`已开始上传 ${acceptedFiles.length} 张；另有${skipped}，已跳过。`);
    }

    for (const file of acceptedFiles) {
      const previewUrl = URL.createObjectURL(file);
      try {
        const asset = await upload.mutateAsync({ file, mediaKind: "image" });
        setSources((current) => [...current, { asset, previewUrl, fileKey: fileKey(file) }]);
      } catch (error) {
        URL.revokeObjectURL(previewUrl);
        setLocalError(errorMessage(error));
      }
    }
  }

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    if (busy || !event.dataTransfer.types.includes("Files")) return;
    dragDepth.current += 1;
    setIsDraggingFiles(true);
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setIsDraggingFiles(false);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    dragDepth.current = 0;
    setIsDraggingFiles(false);
    if (busy) return;
    void addFiles(Array.from(event.dataTransfer.files));
  }

  function removeSource(id: string) {
    setSources((current) => {
      const removed = current.find((source) => source.asset.id === id);
      if (removed) URL.revokeObjectURL(removed.previewUrl);
      return current.filter((source) => source.asset.id !== id);
    });
  }

  async function createPlan() {
    if (!canCreate) return;
    const body: DomesticMainImageJobCreate = {
      type: "domestic_main_image",
      intent: "submit",
      display_name: `国内主图六件套 · ${productName.trim()}`,
      input: { product_asset_ids: sources.map((source) => source.asset.id) },
      configuration: {
        auto_start_after_plan: true,
        ...mainImageSpeedSettings(speed),
        product_name: productName.trim(),
        category: category.trim(),
        selling_points: lines(sellingPoints, 24),
        user_provided_display_facts: lines(displayFacts, 24),
        product_attributes: [],
        scenes: lines(scenes, 12),
        offers: lines(offers, 12),
        service_or_trust_facts: lines(trustFacts, 12),
        style_preferences: lines(style, 12),
        instruction: instruction.trim(),
        language: "zh-CN",
        aurora_shadow_mode: shadowMode,
      },
    };
    try {
      const created = await createJob.mutateAsync(body);
      setCreatedJobId(created.id);
      router.replace(`/workspace/domestic-main-image?jobId=${encodeURIComponent(created.id)}`);
    } catch (error) {
      setLocalError(errorMessage(error));
    }
  }

  async function retryFailedPages() {
    if (!job.data || !recoveryMode || !allowsJobAction(job.data.allowed_actions, "retry")) return;
    setLocalError(undefined);
    try {
      const retried = await retryJob.mutateAsync({
        jobId: job.data.id,
        allowedActions: job.data.allowed_actions,
        scope: "failed",
        itemIds: [],
      });
      router.replace(`/workspace/domestic-main-image?jobId=${encodeURIComponent(retried.id)}`);
    } catch (error) {
      setLocalError(errorMessage(error));
    }
  }

  return (
    <AppShell flush>
      <div className={styles.page}>
        <WorkspaceHeader
          eyebrow="海宇绘品 · 国内电商主图"
          title="主图套图"
          description="上传商品素材，生成完整六张主图，完成后可查看与下载。"
          meta={<span className={styles.secure}><ShieldCheck size={15} />连续创作流程</span>}
        />

        <div className={styles.rail} aria-label="工作流程">
          {["商品素材", "后台规划", fastMode ? "生成图片" : "生成与审图", "整组交付"].map((label, index) => (
            <span key={label} data-active={(index === 0 && !jobId) || (index === 1 && job.data?.status === "awaiting_confirmation") || (index === 2 && job.data?.status === "processing") || (index === 3 && ["awaiting_final_review", "completed"].includes(job.data?.status ?? "") && hasCompleteSet) || undefined}>
              <i>{index + 1}</i>{label}
            </span>
          ))}
        </div>

        <div className={styles.grid}>
          <main>
            <section className={styles.card}>
              <div className={styles.heading}><span>01</span><div><h2>上传商品原图</h2><p>清晰展示外观与细节，多角度图片有助于保持商品一致。</p></div></div>
              {!jobId && <button type="button" className="secondary-button" disabled={busy} onClick={() => setLibraryOpen(true)}>从素材库选择商品原图</button>}
              {jobId ? <div className={styles.uploads}>{restoredAssetIds.map((assetId, index) => <RestoredSource key={assetId} workspaceId={workspace?.id} assetId={assetId} index={index} />)}</div> : <>
                {sources.length < MAX_SOURCE_IMAGES && <div
                  className={styles.dropZone}
                  data-dragging={isDraggingFiles || undefined}
                  data-uploading={upload.isPending || undefined}
                  onDragEnter={handleDragEnter}
                  onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                >
                  <input ref={fileInput} className={styles.fileInput} type="file" multiple accept={IMAGE_UPLOAD_ACCEPT} onChange={(event) => { const files = Array.from(event.currentTarget.files ?? []); event.currentTarget.value = ""; void addFiles(files); }} />
                  <button type="button" className={styles.dropAction} onClick={() => fileInput.current?.click()} disabled={busy}>
                    <span className={styles.dropIcon}>{upload.isPending ? <LoaderCircle className="spin" size={27} /> : <ImagePlus size={27} />}</span>
                    <span><strong>{upload.isPending ? "正在上传商品原图" : isDraggingFiles ? "松开即可加入这些照片" : "把多张商品原图拖到这里"}</strong><small>也可以点击选择 · {IMAGE_UPLOAD_FORMAT_HINT} · 最多 {MAX_SOURCE_IMAGES} 张</small></span>
                    <em>{sources.length} / {MAX_SOURCE_IMAGES}</em>
                  </button>
                </div>}
                {sources.length > 0 && <div className={styles.uploads} aria-live="polite">{sources.map((source, index) => <figure key={source.asset.id}><RestoredSource assetId={source.asset.id} workspaceId={workspace?.id} index={index} /><button type="button" onClick={() => removeSource(source.asset.id)} aria-label={`移除商品素材 ${index + 1}`}><X size={14} /></button></figure>)}</div>}
              </>}
            </section>

            <section className={styles.card}>
              <div className={styles.heading}><span>02</span><div><h2>告诉我们商品的亮点</h2><p>填写商品基本信息，真实卖点和资料越完整，越有助于规划整套图片。</p></div></div>
              <fieldset className={styles.speedChoice} disabled={Boolean(jobId) || busy}>
                <legend>生成版本</legend>
                {MAIN_IMAGE_SPEED_OPTIONS.map(([value, title]) => <label key={value} data-selected={activeSpeed === value || undefined}><input type="radio" name="main-image-speed" value={value} checked={activeSpeed === value} onChange={() => setSpeed(value)} /><span><strong>{title}{value === "native_fast" && <GenerationRecommendation />}</strong></span></label>)}
              </fieldset>
              <ProductFactsGuidance /><div className={styles.form}>
                <label><span>商品名称 *</span><input disabled={Boolean(jobId)} value={jobId ? restoredString("product_name") : productName} onChange={(event) => setProductName(event.target.value)} placeholder="例如：轻量保温随行杯" /></label>
                <label><span>商品类目 *</span><input disabled={Boolean(jobId)} value={jobId ? restoredString("category") : category} onChange={(event) => setCategory(event.target.value)} placeholder="例如：杯壶 / 保温杯" /></label>
                <label className={styles.full}><span>核心卖点 * <small>换行分隔</small></span><textarea disabled={Boolean(jobId)} value={jobId ? restoredLines("selling_points") : sellingPoints} onChange={(event) => setSellingPoints(event.target.value)} placeholder={'轻量便携\n杯盖防误开\n食品接触级内胆'} /></label>
              </div>
              <FormDisclosure title="补充商品细节" summary={[displayFacts, scenes, offers, trustFacts].filter(value => value.trim()).length ? `已补充 ${[displayFacts, scenes, offers, trustFacts].filter(value => value.trim()).length} 项信息` : "选填 · 规格、使用场景、优惠与服务"} defaultOpen={Boolean(jobId)}>
              <div className={styles.form}>
                <label className={styles.full}><span>图片中清晰可读的事实 <small>逐行转录</small></span><textarea disabled={Boolean(jobId)} value={jobId ? restoredLines("user_provided_display_facts") : displayFacts} onChange={(event) => setDisplayFacts(event.target.value)} placeholder={'12V 电瓶\n双座\n清晰可见的原图标题、数字或规格'} /></label>
                <label><span>真实使用场景</span><textarea disabled={Boolean(jobId)} value={jobId ? restoredLines("scenes") : scenes} onChange={(event) => setScenes(event.target.value)} placeholder="通勤、办公室、短途出行" /></label>
                <label><span>优惠或条件事实</span><textarea disabled={Boolean(jobId)} value={jobId ? restoredLines("offers") : offers} onChange={(event) => setOffers(event.target.value)} placeholder="仅填写原图或业务资料中明确存在的优惠与条件" /></label>
                <label><span>服务或信任事实</span><textarea disabled={Boolean(jobId)} value={jobId ? restoredLines("service_or_trust_facts") : trustFacts} onChange={(event) => setTrustFacts(event.target.value)} placeholder="仅填写能够证实的检测、质保或服务" /></label>
              </div>
              </FormDisclosure>
              <FormDisclosure title="视觉与文案偏好" summary={[style, instruction].filter(value => value.trim()).length ? "已添加创作偏好" : "选填 · 风格、色调与需要保留的细节"} defaultOpen={Boolean(jobId)}>
              <div className={styles.form}>
                <label><span>视觉偏好</span><textarea disabled={Boolean(jobId)} value={jobId ? restoredLines("style_preferences") : style} onChange={(event) => setStyle(event.target.value)} placeholder="克制、高级、暖灰背景、清晰商品轮廓" /></label>
                <label><span>补充要求</span><textarea disabled={Boolean(jobId)} value={jobId ? restoredString("instruction") : instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="不能改变的结构、Logo、颜色或包装信息" /></label>
              </div>
              </FormDisclosure>
              {runtime.data?.environment === "development" && <details className="creation-dev-options"><summary>本地开发选项</summary><label className={styles.full}><span>本地生成预览</span><span className={styles.shadowToggle}><select disabled={Boolean(jobId)} value={activeShadowMode} onChange={(event) => setShadowMode(event.target.value as AuroraShadowMode)}><option value="off">关闭</option><option value="contract_first_rebuild_v1">contract-first v1</option><option value="execution_semantics_v1">execution semantics v1</option></select><span><strong>Shadow 构图模式</strong><small>仅本地开发提交使用；execution semantics v1 会强制页面空间层级和反复骨架约束。</small></span></span></label></details>}
            </section>

            {!automaticPlan && <section className={styles.card}>
              <div className={styles.heading}><span>03</span><div><h2>六图方案确认</h2><p>确认后锁定商品身份、事实边界和整套视觉世界。</p></div>{job.data && <StatusBadge status={job.data.status} />}</div>
              {!plan.length ? <div className={styles.empty}>创建任务后，会先分析商品并锁定身份，再编译 1 张搜索主图与 5 张轮播图的完整方案。</div> : <>
                <div className={styles.identityLock}>
                  <span>PRODUCT IDENTITY LOCK</span>
                  <h3>{identityLock?.product_form ?? "当前商品身份已锁定"}</h3>
                  {<><p>{identityLock?.identity_summary}</p>
                  <div><strong>必须保持</strong><p>{identityLock?.must_preserve?.join("；")}</p></div>
                  <div><strong>禁止改变</strong><p>{identityLock?.must_not_change?.join("；")}</p></div></>}
                </div>

                <div className={styles.campaignWorld}>
                  <div className={styles.worldTitle}><span>整组视觉世界</span><strong>六张图共享一套美术方向，版式与镜头按页面职责变化</strong></div>
                  {<dl>
                    <div><dt>色彩角色</dt><dd>{styleSummary(campaignStyle?.palette_roles)}</dd></div>
                    <div><dt>字体气质</dt><dd>{styleSummary(campaignStyle?.typography)}</dd></div>
                    <div><dt>图形 / 信息面</dt><dd>{campaignStyle?.graphic_language}；{campaignStyle?.information_surface_geometry}</dd></div>
                    <div><dt>摄影完成度</dt><dd>{campaignStyle?.photography_finish}</dd></div>
                    <div><dt>光线与色温</dt><dd>{campaignStyle?.light_direction}；{campaignStyle?.color_temperature}</dd></div>
                    <div><dt>整组密度节奏</dt><dd>{campaignStyle?.density_band}</dd></div>
                  </dl>}
                </div>

                <div className={styles.contractLegend} aria-label="六页执行契约字段">
                  {<><span>主卖点</span><span>辅助卖点</span><span>画面证明</span><span>构图与主体</span><span>阅读路径</span><span>不重复理由</span><span>商业闭合</span></>}
                </div>
                <div className={styles.plan}>{plan.map((page) => <article key={page.page_index}><i>{String(page.page_index + 1).padStart(2, "0")}</i><div><span>{roleNames[page.page_role] ?? page.page_role}</span><strong>{page.main_selling_point}</strong>{page.buyer_question && <p>{page.buyer_question}</p>}{<dl>
                  <div><dt>主卖点</dt><dd>{page.main_selling_point}</dd></div>
                  <div><dt>辅助卖点</dt><dd>{page.supporting_selling_points?.length ? page.supporting_selling_points.join(" · ") : "无强制辅助卖点，按画面需要取舍"}</dd></div>
                  <div><dt>画面证明</dt><dd>{page.visual_proof}</dd></div>
                  <div><dt>构图主体</dt><dd>{page.composition_and_subject}</dd></div>
                  <div><dt>阅读路径</dt><dd>{page.reading_path_and_type_hierarchy}</dd></div>
                  <div><dt>不重复</dt><dd>{page.non_repetition_reason}</dd></div>
                  <div><dt>商业闭合</dt><dd>{page.commercial_closure}</dd></div>
                </dl>}</div>{page.page_index === 0 ? <em>搜索入口</em> : <em>轮播</em>}</article>)}</div>
              </>}
            </section>}

            {showGeneration && <section className={styles.card}><div className={styles.heading}><span>04</span><div><h2>逐张生成进度</h2><p>{fastMode ? "六张图独立生成，完成一张即可查看与下载。" : "逐张生成并审核，通过整组审核后自动交付。"}</p></div></div>{terminalIncomplete && <div className={styles.generationFailure} role="alert"><AlertTriangle size={20} /><div><strong>{setAuditFailed && hasAllGeneratedImages ? "六张图片已生成，但整组审核未通过" : "任务已经终止，不再继续运行"}</strong><p>{setAuditFailed && hasAllGeneratedImages ? "图片资产完整保留，但整组审核未通过，因此暂不能进入整组确认。" : failedItem ? `第 ${failedItem.ordinal + 1} 页：${domesticFailureMessage(failedItem.error_message)}` : "任务没有完成全部六张图片。"}{blockedItemCount ? ` 后续 ${blockedItemCount} 页因顺序生成规则没有执行。` : ""}</p><small>{recoveryMode === "replan" ? "修复原因后，可点击右侧“重新规划并生成”；商品素材与参数将保留。" : recoveryMode === "retry" ? "修复原因后，可点击右侧“按原方案重新生成”。" : "请核对任务失败原因。"}</small></div></div>}<div className={styles.generationProgress}><strong>{hasAllGeneratedImages ? "6 / 6 张图片已生成" : fastMode ? `${acceptedResultCount} / 6 张已生成` : `${acceptedResultCount} / 6 张已通过审核`}</strong>{!fastMode && hasAllGeneratedImages && acceptedResultCount < 6 && <small className={styles.auditNote}>{setAuditFailed ? "整组审核未通过，但六张图片均已生成" : "整组审核未通过，当前任务仍为部分完成"}</small>}<div>{job.data!.items.map((item) => <span key={item.id} data-status={item.error_code === "AURORA_SET_AUDIT_FAILED" ? "set-audit-failed" : item.status}><i>{String(item.ordinal + 1).padStart(2, "0")}</i>{roleNames[plan[item.ordinal]?.page_role] ?? `图 ${item.ordinal + 1}`}<em>{generationItemLabel(item.status, item.result_source, fastMode)}</em></span>)}</div></div>{job.data?.status === "completed" && !hasCompleteSet && <div className={styles.mockWarning}><strong>没有生成真实图片</strong><p>这条任务由安全 Mock 执行，只模拟了六页状态，不能进入整组图片确认。启用真实 Provider 后请重新运行任务。</p></div>}</section>}

            {results.length > 0 && <section className={styles.card}><div className={styles.heading}><span>05</span><div><h2>六图交付</h2><p>{job.data?.status === "awaiting_final_review" ? "整组审计已完成，请逐张检查后确认交付。" : "单张可查看与下载，全部完成后可下载整套文件。"}</p></div></div>{!fastMode && finalReview && (!finalReview.set_audit_passed || finalReview.residual_issues.length > 0) && <div className={styles.generationFailure} role="alert"><AlertTriangle size={20} /><div><strong>确认前请检查以下未解决的问题</strong>{finalReview.residual_issues.map((issue, index) => <p key={index}>{typeof issue.page_index === "number" ? `第 ${issue.page_index + 1} 页：` : ""}{issue.decision_summary}</p>)}{!finalReview.set_audit_passed && <p>整组页面职责区分未通过审计，请检查搜索主图是否足够明确。确认交付表示接受这些问题。</p>}</div></div>}<div className={styles.results}>{results.map((asset) => <JobResultImage key={asset.asset_id} workspaceId={workspace?.id} assetId={asset.asset_id} alt={`国内电商主图 ${asset.ordinal + 1}`} label={roleNames[plan.find((page) => page.page_index === asset.ordinal)?.page_role ?? ""] ?? `图 ${asset.ordinal + 1}`} />)}</div>{job.data?.status === "completed" && hasCompleteSet && <JobDeliverablesDownload workspaceId={workspace?.id} jobId={job.data.id} />}</section>}
          </main>

          <aside className={styles.side}>
            {handoff.error && <p className={styles.error} role="alert">{errorMessage(handoff.error)}</p>}
            {localError || createJob.error || confirmPlan.error || finalizeJob.error || retryJob.error ? <p className={styles.error}>{localError ?? errorMessage(createJob.error ?? confirmPlan.error ?? finalizeJob.error ?? retryJob.error)}</p> : null}
            {!jobId && <CreditCostHint jobType="domestic_main_image" count={6} variant={fastMode ? "fast" : undefined} />}
            {!jobId ? <button className={styles.primary} disabled={!canCreate || busy} onClick={() => void createPlan()}>{busy ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}生成主图六件套<ArrowRight size={17} /></button> : job.data && !automaticPlan && allowsJobAction(job.data.allowed_actions, "finalize") ? <button className={styles.primary} disabled={finalizeJob.isPending} onClick={() => finalizeJob.mutate({ jobId: job.data!.id, allowedActions: job.data!.allowed_actions, approvalText: "我已审阅六张成图，确认整组结果并批准按 Aurora Skill 完成交付。" })}>{finalizeJob.isPending ? <LoaderCircle className="spin" size={17} /> : <ShieldCheck size={17} />}确认整组并完成交付<ArrowRight size={17} /></button> : job.data && recoveryMode && !hasCompleteSet && providerReady ? <button className={styles.primary} disabled={retryJob.isPending} onClick={() => void retryFailedPages()}>{retryJob.isPending ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}{recoveryMode === "replan" ? "重新规划并生成" : "按原方案重新生成"}<ArrowRight size={17} /></button> : !automaticPlan && job.data?.plan && allowsJobAction(job.data.allowed_actions, "confirm_plan") && providerReady ? <button className={styles.primary} disabled={confirmPlan.isPending} onClick={() => confirmPlan.mutate({ jobId: job.data!.id, revision: job.data!.plan!.revision, allowedActions: job.data!.allowed_actions, approvalText: fastMode ? "我确认当前快速六图规划，授权独立生成并直接交付，成图未经独立 AI 复审。" : "我确认当前六图规划，并授权按 Aurora Skill 逐张生成、审图及执行每页至多一次自动修复。" })}>{confirmPlan.isPending ? <LoaderCircle className="spin" size={17} /> : <ShieldCheck size={17} />}确认方案并开始<ArrowRight size={17} /></button> : <button className={styles.primary} disabled><LoaderCircle className={job.data && ["queued", "planning", "processing"].includes(job.data.status) ? "spin" : ""} size={17} />{terminalIncomplete ? "任务已终止" : automaticPlan && job.data?.status === "awaiting_final_review" ? "正在整理交付" : job.data?.status === "completed" && hasCompleteSet ? "整组已完成" : job.data?.status === "completed" ? "未生成真实图片" : workflow.mode === "mock" && job.data?.status === "awaiting_confirmation" ? "真实生成服务未启用" : "任务处理中"}</button>}
            <small className={styles.note}>{providerReady ? !sources.length ? "请先上传或选择商品原图。" : !productName.trim() ? "请填写商品名称。" : !category.trim() ? "请填写商品类目。" : !sellingPoints.trim() ? "请填写真实卖点与商品资料，帮助规划整套图片。" : "已准备好，图片完成后可逐张查看与下载。" : "图片生成服务暂未启用，当前仅可预览方案。"}</small>
          </aside>
        </div>
      </div>
    {libraryOpen && <AssetLibraryDialog workspaceId={workspace?.id} title="选择商品原图" description="复用同一商品的清晰素材，最多 8 张。" selectedIds={sources.map(source => source.asset.id)} maxItems={8} onClose={() => setLibraryOpen(false)} onConfirm={(ids, assets) => { setSources(ids.map(id => sources.find(source => source.asset.id === id) ?? { asset: assets.find(asset => asset.id === id)!, previewUrl: "", fileKey: id }).filter(source => Boolean(source.asset))); setLibraryOpen(false); }} />}
    </AppShell>
  );
}
