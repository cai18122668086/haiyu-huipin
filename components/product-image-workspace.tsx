"use client";

import { useDraftState } from "@/lib/hooks/use-creation-draft";
import { FormDisclosure } from "@/components/form-disclosure";
import { ProductFactsGuidance } from "@/components/product-facts-guidance";

import { CreditCostHint } from "@/features/billing/components/credit-account";

import {
  ArrowRight,
  CheckCircle2,
  Clock3,
  ImagePlus,
  Info,
  Images,
  LoaderCircle,
  Palette,
  RefreshCcw,
  Sparkles,
  Upload,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { ImageDropZone } from "@/components/image-drop-zone";
import { ReplicaSelect } from "@/components/replica-select";
import { JobProgressFeedback } from "@/components/job-progress-feedback";
import { AppShell } from "@/components/app-shell";
import { WorkspaceJobGate, WorkspaceJobInputs } from "@/components/workspace-job-gate";
import { useWorkspaceJobId, useWorkspaceJobReview } from "@/lib/jobs/use-workspace-job-id";
import { workspaceJobSnapshot } from "@/lib/jobs/workspace-restore";
import { AssetLibraryDialog } from "@/components/asset-library-picker";
import { ResultGrid } from "@/components/result-grid";
import { SkillReviewChecklist } from "@/components/skill-review-checklist";
import { StatusBadge } from "@/components/status-badge";
import { WorkspaceHeader } from "@/components/workspace-header";
import { useUploadAsset } from "@/features/assets/api/assets";
import { useCreateJob, useJob, useRetryJob } from "@/features/jobs/api/jobs";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { errorMessage } from "@/lib/api/request";
import { IMAGE_UPLOAD_FORMAT_HINT } from "@/lib/image-upload";
import type { ProductImageJobCreate } from "@/lib/api/types";
import {
  PRODUCT_IMAGE_FEATURES,
  getProductImageFeatureByPresetSlug
} from "@/lib/feature-registry";
import { selectJobResultAssets } from "@/lib/jobs/results";
import { allowsJobAction } from "@/lib/jobs/actions";
import {
  presentWorkflow,
  resolveRuntimeCapability,
  useRuntimeStatus,
} from "@/features/runtime/api/runtime";
import styles from "./product-image-workspace.module.css";

type AspectRatio = "1:1" | "3:4" | "4:3" | "9:16" | "16:9";

const aspectRatios: { value: AspectRatio; label: string; note: string }[] = [
  { value: "1:1", label: "1:1", note: "方形主图" },
  { value: "3:4", label: "3:4", note: "电商竖图" },
  { value: "4:3", label: "4:3", note: "横向展示" },
  { value: "9:16", label: "9:16", note: "短视频封面" },
  { value: "16:9", label: "16:9", note: "横屏视觉" },
];

const platforms = [
  { value: "tmall", label: "天猫 / 淘宝" },
  { value: "jd", label: "京东" },
  { value: "douyin", label: "抖音电商" },
  { value: "xiaohongshu", label: "小红书" },
  { value: "amazon-us", label: "Amazon US" },
  { value: "generic", label: "通用渠道" },
];

function UploadPanel({
  title,
  description,
  count,
  required,
  uploading,
  disabled = false,
  uploadLabel = "本地上传",
  onFiles,
  onOpenLibrary,
}: {
  title: string;
  description: string;
  count: number;
  required?: boolean;
  uploading: boolean;
  disabled?: boolean;
  uploadLabel?: string;
  onFiles: (files: File[]) => void | Promise<void>;
  onOpenLibrary?: () => void;
}) {
  return (
    <ImageDropZone label={title} className={styles.uploadPanel} multiple maxFiles={Math.max(0, 8 - count)}
      disabled={disabled} busy={uploading} onFiles={onFiles}>
      {({ openFileDialog, disabled: uploadDisabled, busy: uploadingFile }) => <>
        <div className={styles.uploadIcon}><ImagePlus size={22} /></div>
        <div className={styles.uploadCopy}>
          <div>
            <strong>{title}</strong>
            {required ? <span>必填</span> : <span data-optional>可选</span>}
          </div>
          <p>{description}</p>
          <small>{count ? `已选择 ${count} 张素材` : IMAGE_UPLOAD_FORMAT_HINT}</small>
        </div>
        <div className={styles.uploadActions} data-has-library={Boolean(onOpenLibrary) || undefined}>
          <button
            type="button"
            onClick={openFileDialog}
            disabled={uploadDisabled}
          >
            {uploadingFile ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}
            {uploadLabel}
          </button>
          {onOpenLibrary ? (
            <button
              type="button"
              data-library
              aria-haspopup="dialog"
              onClick={onOpenLibrary}
              disabled={disabled || uploadingFile}
            >
              <Images size={16} />
              从素材库选择
            </button>
          ) : null}
        </div>
      </>}
    </ImageDropZone>
  );
}

export function ProductImageWorkspace({ preset }: { preset: string }) {
  const router = useRouter();
  const feature = getProductImageFeatureByPresetSlug(preset);
  const [jobId, setJobId] = useWorkspaceJobId(feature?.route ?? `/workspace/product-image/${preset}`);
  const { workspace, isPending: workspacePending, error: workspaceError } = useWorkspace();
  const upload = useUploadAsset(workspace?.id);
  const [draftProductAssetIds, setProductAssetIds] = useDraftState<string[]>(workspace?.id, feature?.id ?? "product-image", "draftProductAssetIds", []);
  const [draftReferenceAssetIds, setReferenceAssetIds] = useDraftState<string[]>(workspace?.id, feature?.id ?? "product-image", "draftReferenceAssetIds", []);
  const [productLibraryOpen, setProductLibraryOpen] = useState(false);
  const [referenceLibraryOpen, setReferenceLibraryOpen] = useState(false);
  const [draftPlatformCode, setPlatformCode] = useDraftState(workspace?.id, feature?.id ?? "product-image", "draftPlatformCode", "tmall");
  const [draftAspectRatio, setAspectRatio] = useDraftState<AspectRatio>(workspace?.id, feature?.id ?? "product-image", "draftAspectRatio", "1:1");
  const [draftImageCount, setImageCount] = useDraftState(workspace?.id, feature?.id ?? "product-image", "draftImageCount", 1);
  const [draftInstruction, setInstruction] = useDraftState(workspace?.id, feature?.id ?? "product-image", "draftInstruction", "");
  const [draftTargetColor, setTargetColor] = useDraftState(workspace?.id, feature?.id ?? "product-image", "draftTargetColor", "");
  const [uiError, setUiError] = useState<string>();
  const [humanReviewComplete, setHumanReviewComplete] = useWorkspaceJobReview(jobId);
  const runtime = useRuntimeStatus();
  const capability = resolveRuntimeCapability(runtime.data, feature?.runtimeCapability ?? "product_image");
  const workflow = presentWorkflow(capability);
  const createJob = useCreateJob(workspace?.id);
  const retryJob = useRetryJob(workspace?.id);
  const job = useJob(workspace?.id, jobId);
  const saved = workspaceJobSnapshot(job.data);
  const productAssetIds = jobId ? saved.ids("product_asset_ids") : draftProductAssetIds;
  const referenceAssetIds = jobId ? saved.ids("reference_asset_ids") : draftReferenceAssetIds;
  const platformCode = jobId ? saved.text("platform_code") : draftPlatformCode;
  const aspectRatio = jobId ? saved.choice("aspect_ratio", aspectRatios.map((ratio) => ratio.value), "1:1") : draftAspectRatio;
  const imageCount = jobId ? saved.number("image_count", job.data?.expected_item_count ?? 1) : draftImageCount;
  const instruction = jobId ? saved.text("instruction") : draftInstruction;
  const targetColor = jobId ? saved.text("target_color") : draftTargetColor;
  const resultAssets = selectJobResultAssets(job.data);
  const requestError = workspaceError ?? upload.error ?? job.error ?? retryJob.error;

  const progressText = useMemo(() => {
    if (!job.data) return "正在准备生成";
    return `${job.data.succeeded_item_count} / ${job.data.expected_item_count} 张已完成`;
  }, [job.data]);

  if (!feature) return null;

  const addUploadedAssets = async (files: File[], role: "product" | "reference") => {
    setUiError(undefined);
    const uploadedIds: string[] = [];
    const failures: string[] = [];
    for (const file of files.slice(0, 8)) {
      try {
        const asset = await upload.mutateAsync({ file, mediaKind: "image" });
        uploadedIds.push(asset.id);
      } catch (error) {
        failures.push(`${file.name || "所选图片"}：${errorMessage(error)}`);
      }
    }
    if (uploadedIds.length) {
      if (role === "product") {
        setProductAssetIds((current) =>
          [...new Set([...current, ...uploadedIds])].slice(0, 8),
        );
        setReferenceAssetIds((current) =>
          current.filter((id) => !uploadedIds.includes(id)),
        );
      } else {
        setReferenceAssetIds((current) =>
          [...new Set([...current, ...uploadedIds])].slice(0, 8),
        );
        setProductAssetIds((current) =>
          current.filter((id) => !uploadedIds.includes(id)),
        );
      }
    }
    if (failures.length) {
      const uploadedNote = uploadedIds.length ? `已成功上传 ${uploadedIds.length} 张；` : "";
      const remaining = failures.length > 1 ? `，另有 ${failures.length - 1} 张上传失败` : "";
      setUiError(`${uploadedNote}${failures[0]}${remaining}`);
    }
  };

  const targetColorMissing = feature.taskPreset === "change_color" && !targetColor.trim();
  const canSubmit = Boolean(
    !jobId && workspace &&
    productAssetIds.length &&
    !targetColorMissing &&
    workflow.canSubmit &&
    !runtime.isPending &&
    !upload.isPending &&
    !createJob.isPending,
  );

  const retryFailedImages = async () => {
    if (!job.data || !allowsJobAction(job.data.allowed_actions, "retry")) return;
    setUiError(undefined);
    setHumanReviewComplete(false);
    try {
      const retried = await retryJob.mutateAsync({
        jobId: job.data.id,
        allowedActions: job.data.allowed_actions,
        scope: "failed",
      });
      setJobId(retried.id);
    } catch (error) {
      setUiError(errorMessage(error));
    }
  };

  const submit = async () => {
    if (!canSubmit) return;
    setUiError(undefined);
    const body: ProductImageJobCreate = {
      type: "product_image",
      intent: "submit",
      display_name: feature.title,
      input: {
        product_asset_ids: productAssetIds,
        reference_asset_ids: referenceAssetIds,
      },
      configuration: {
        task_type: feature.taskPreset,
        platform_code: platformCode,
        aspect_ratio: aspectRatio,
        image_count: imageCount,
        instruction: instruction.trim(),
        ...(feature.taskPreset === "change_color"
          ? { target_color: targetColor.trim() }
          : {}),
      },
    };
    try {
      const created = await createJob.mutateAsync(body);
      setHumanReviewComplete(false);
      setJobId(created.id);
    } catch (error) {
      setUiError(errorMessage(error));
    }
  };

  const resetTask = () => {
    setJobId(undefined);
    setUiError(undefined);
    setHumanReviewComplete(false);
    createJob.reset();
    retryJob.reset();
  };

  return (
    <WorkspaceJobGate jobId={jobId} job={job.data} error={job.error} onRetry={() => void job.refetch()} expectedType="product_image" expectedPreset={feature.taskPreset}>
    <AppShell flush sidebar={false}>
      <div className={styles.workspacePage}>
        <WorkspaceHeader
          jobId={jobId}
          eyebrow="万能生图 · 统一工作台"
          title={feature.title}
          description={feature.description}
        />

        <nav className={styles.stepBar} aria-label="创作步骤">
          {[
            ["01", "选择素材"],
            ["02", "生成设置"],
            ["03", "开始生成"],
            ["04", "生成结果"],
          ].map(([number, label], index) => (
            <span
              key={number}
              data-active={
                index === 0 ||
                (index === 1 && productAssetIds.length > 0) ||
                (index >= 2 && Boolean(jobId)) ||
                undefined
              }
            >
              <i>{number}</i>{label}
            </span>
          ))}
        </nav>

        <JobProgressFeedback pending={createJob.isPending && !jobId} />

        <div className={styles.workspaceGrid} data-single-column>
          <main className={styles.mainColumn}>
            <section className={styles.card}>
              <div className={styles.sectionHeading}>
                <div>
                  <span>01</span>
                  <div><h2>选择创作素材</h2><p>支持本地上传和当前工作区素材库。</p></div>
                </div>
                <strong>{productAssetIds.length + referenceAssetIds.length} 张已选</strong>
              </div>

              <div className={styles.uploadGrid}>
                <UploadPanel
                  title="商品图"
                  description="上传清晰、主体完整的商品参考图，可提供多个角度。"
                  count={productAssetIds.length}
                  required
                  uploading={upload.isPending}
                  disabled={!workspace || Boolean(jobId)}
                  uploadLabel="上传图片"
                  onFiles={(files) => addUploadedAssets(files, "product")}
                  onOpenLibrary={() => setProductLibraryOpen(true)}
                />
                <UploadPanel
                  title="参考图"
                  description="可选场景、背景、模特或颜色参考，不会替代商品主体。"
                  count={referenceAssetIds.length}
                  uploading={upload.isPending}
                  disabled={!workspace || Boolean(jobId)}
                  uploadLabel="上传图片"
                  onFiles={(files) => addUploadedAssets(files, "reference")}
                  onOpenLibrary={() => setReferenceLibraryOpen(true)}
                />
              </div>
              {jobId && <WorkspaceJobInputs workspaceId={workspace?.id} job={job.data} />}
            </section>

            <section className={styles.card}>
              <div className={styles.sectionHeading}>
                <div>
                  <span>02</span>
                  <div><h2>配置生成任务</h2><p>选择创作参数，快速生成符合电商平台需求的商品图片。</p></div>
                </div>
                <strong>{feature.shortTitle}</strong>
              </div>

              <FormDisclosure title="输出设置" summary={`${platforms.find(item => item.value === platformCode)?.label ?? "目标平台"} · ${aspectRatio} · ${imageCount} 张`} defaultOpen={Boolean(jobId)}>
              <div className={styles.formGrid}>
                <label>
                  <span>创作功能</span>
                  <ReplicaSelect label="创作功能" value={feature.presetSlug} onChange={value => router.push(`/workspace/product-image/${value}`)} disabled={Boolean(jobId)} options={PRODUCT_IMAGE_FEATURES.map(item => [item.presetSlug, item.title] as const)} />
                </label>
                <label>
                  <span>目标平台</span>
                  <ReplicaSelect label="目标平台" value={platformCode} onChange={setPlatformCode} disabled={Boolean(jobId)} options={platforms.map(platform => [platform.value, platform.label] as const)} />
                </label>
              </div>

              <fieldset className={styles.optionGroup} disabled={Boolean(jobId)}>
                <legend>画面比例</legend>
                <div className={styles.ratioGrid}>
                  {aspectRatios.map((ratio) => (
                    <label key={ratio.value} data-selected={aspectRatio === ratio.value || undefined}>
                      <input
                        type="radio"
                        name="aspect-ratio"
                        value={ratio.value}
                        checked={aspectRatio === ratio.value}
                        onChange={() => setAspectRatio(ratio.value)}
                      />
                      <span className={styles.ratioPreview} aria-hidden="true"><i style={{ aspectRatio: ratio.value.replace(":", " / ") }} /></span>
                      <strong>{ratio.label}</strong>
                      <small>{ratio.note}</small>
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset className={styles.optionGroup} disabled={Boolean(jobId)}>
                <legend>生成数量</legend>
                <div className={styles.countOptions}>
                  {[1, 2, 3, 4].map((count) => (
                    <label key={count} data-selected={imageCount === count || undefined}>
                      <input
                        type="radio"
                        name="image-count"
                        value={count}
                        checked={imageCount === count}
                        onChange={() => setImageCount(count)}
                      />
                      <strong>{count}</strong><span>张</span>
                    </label>
                  ))}
                </div>
              </fieldset>

              </FormDisclosure>
              {feature.taskPreset === "change_color" ? (
                <label className={styles.colorField}>
                  <span><Palette size={16} />目标颜色 <i>必填</i></span>
                  <input
                    value={targetColor}
                    onChange={(event) => setTargetColor(event.target.value)}
                    placeholder="例如：Pantone 186 C、哑光酒红色、#B31942"
                    disabled={Boolean(jobId)}
                    aria-invalid={targetColorMissing}
                  />
                  <small>可输入色名、色号或颜色描述；为空时不会创建任务。</small>
                </label>
              ) : null}

              <label className={styles.instructionField}>
                <span>详细描述你的要求</span><ProductFactsGuidance editing />
                <textarea
                  value={instruction}
                  onChange={(event) => setInstruction(event.target.value)}
                  placeholder={feature.guidance}
                  rows={5}
                  maxLength={4000}
                  disabled={Boolean(jobId)}
                />
                <small>{instruction.length} / 4000 · {feature.guidance}</small>
              </label>
            </section>

            {jobId ? (
              <section className={styles.card}>
                <div className={styles.sectionHeading}>
                  <div>
                    <span>03</span>
                    <div><h2>生成进度</h2><p>生成进度会自动更新。</p></div>
                  </div>
                  {job.data ? <StatusBadge status={job.data.status} /> : null}
                </div>
                {job.isPending ? (
                  <div className={styles.jobState}><LoaderCircle className="spin" />正在读取任务…</div>
                ) : job.error && !job.data ? (
                  <div className={styles.jobError} role="alert">{errorMessage(job.error)}</div>
                ) : job.data ? (
                  <div className={styles.jobPanel}>
                    <JobProgressFeedback job={job.data} error={job.error} lastSyncedAt={job.dataUpdatedAt}
                      refreshing={job.isFetching} onRefresh={() => void job.refetch()} />
                    <div className={styles.itemGrid}>
                      {job.data.items.map((item, index) => (
                        <span key={item.id} data-status={item.status}>
                          <i /> 图片 {index + 1}
                        </span>
                      ))}
                    </div>
                    {allowsJobAction(job.data.allowed_actions, "retry") &&
                    ["failed", "partial", "completed"].includes(job.data.status) ? (
                      <button
                        type="button"
                        className={styles.secondaryAction}
                        onClick={() => void retryFailedImages()}
                        disabled={retryJob.isPending}
                      >
                        {retryJob.isPending ? <LoaderCircle className="spin" size={16} /> : <RefreshCcw size={16} />}
                        保留原素材与 Skill 约束重试失败项
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </section>
            ) : null}

            <ResultGrid
              workspaceId={workspace?.id}
              assets={resultAssets}
              expectedCount={job.data?.expected_item_count ?? imageCount}
              title={`${feature.title}结果`}
            />
            {job.data && ["completed", "partial"].includes(job.data.status) && resultAssets.length > 0 ? (
              <SkillReviewChecklist key={jobId}
                items={[
                  { key: "fidelity", label: "商品仍是同一 SKU，轮廓、结构、颜色、材质、包装、Logo 和关键细节没有跑偏。" },
                  { key: "composition", label: "商品比例、透视和场景关系自然，背景或道具没有遮挡主体。" },
                  { key: "legibility", label: "缩略图和移动端首屏仍看得清商品，没有乱码、密集小字或低清伪影。" },
                  { key: "compliance", label: "没有虚假认证、销量、评价、功效、价格、徽章、水印或未授权额外元素。" },
                ]}
                description={
                  humanReviewComplete
                    ? "已完成当前 Skill 的人工检查；如需更稳上架，仍可再做一版保守白底/灰底图。"
                    : undefined
                }
                onCompleteChange={setHumanReviewComplete}
              />
            ) : null}
          </main>

        </div>

        {productLibraryOpen ? (
          <AssetLibraryDialog
            workspaceId={workspace?.id}
            title="选择商品图"
            description="从个人素材库中选择清晰、主体完整的商品图片。"
            selectedIds={productAssetIds}
            excludedIds={referenceAssetIds}
            onClose={() => setProductLibraryOpen(false)}
            onConfirm={(assetIds) => {
              setProductAssetIds(assetIds);
              setReferenceAssetIds((current) => current.filter((id) => !assetIds.includes(id)));
              setProductLibraryOpen(false);
            }}
            disabled={Boolean(jobId)}
          />
        ) : null}

        {referenceLibraryOpen ? (
          <AssetLibraryDialog
            workspaceId={workspace?.id}
            title="选择参考图"
            description="选择用于场景、背景、风格或光线参考的图片。"
            selectedIds={referenceAssetIds}
            excludedIds={productAssetIds}
            onClose={() => setReferenceLibraryOpen(false)}
            onConfirm={(assetIds) => {
              setReferenceAssetIds(assetIds);
              setProductAssetIds((current) => current.filter((id) => !assetIds.includes(id)));
              setReferenceLibraryOpen(false);
            }}
            disabled={Boolean(jobId)}
          />
        ) : null}

        <footer className={styles.stickyBar}>
          <div>
            <span>{workspacePending ? <LoaderCircle className="spin" size={17} /> : <Clock3 size={17} />}</span>
            <p>
              <strong>{workspace?.name ?? "正在连接工作区"}</strong>
              <small>{jobId ? progressText : `预计生成 ${imageCount} 张图片`}</small>
              {!jobId && <CreditCostHint jobType="product_image" taskPreset={feature.taskPreset} count={imageCount} />}
            </p>
          </div>
          <div className={styles.stickyActions}>
            {(uiError || requestError || createJob.error) ? (
              <p role="alert"><Info size={15} />{uiError ?? errorMessage(requestError ?? createJob.error)}</p>
            ) : !workflow.canSubmit ? (
              <p role="alert"><Info size={15} />{workflow.message}</p>
            ) : !productAssetIds.length ? (
              <p><Info size={15} />请至少选择 1 张商品图。</p>
            ) : targetColorMissing ? (
              <p><Info size={15} />商品换色必须填写目标颜色。</p>
            ) : (
              <p><CheckCircle2 size={15} />素材与参数已就绪。</p>
            )}
            {jobId ? (
              <button type="button" className={styles.secondaryAction} onClick={resetTask}>
                <RefreshCcw size={16} />新建任务
              </button>
            ) : (
              <button
                type="button"
                className={styles.primaryAction}
                disabled={!canSubmit}
                onClick={() => void submit()}
              >
                {createJob.isPending ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}
                {createJob.isPending ? "正在创建" : `生成 ${imageCount} 张`}
                <ArrowRight size={17} />
              </button>
            )}
          </div>
        </footer>
      </div>
    </AppShell>
    </WorkspaceJobGate>
  );
}
