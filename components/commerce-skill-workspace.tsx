"use client";

import { CreditCostHint } from "@/features/billing/components/credit-account";

import {
  ArrowRight,
  CheckCircle2,
  Clock3,
  ImagePlus,
  Images,
  Info,
  LoaderCircle,
  RefreshCcw,
  ShieldCheck,
  Sparkles,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { ImageDropZone } from "@/components/image-drop-zone";
import { ReplicaSelect } from "@/components/replica-select";
import { JobProgressFeedback } from "@/components/job-progress-feedback";
import { GenerationRecommendation } from "@/components/generation-recommendation";
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
import { useConfirmPlan, useCreateJob, useJob } from "@/features/jobs/api/jobs";
import {
  presentWorkflow,
  resolveRuntimeCapability,
  useRuntimeStatus,
} from "@/features/runtime/api/runtime";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { errorMessage } from "@/lib/api/request";
import type {
  AmazonVisualSuiteJobCreate,
  CreativeImageJobCreate,
  ReferenceRemixJobCreate,
} from "@/lib/api/types";
import { getFeatureById } from "@/lib/feature-registry";
import { IMAGE_UPLOAD_FORMAT_HINT, validateImageUpload } from "@/lib/image-upload";
import { allowsJobAction } from "@/lib/jobs/actions";
import { amazonSuiteSpeedConfiguration, amazonSuiteSpeedDraftKey, readAmazonSuiteSpeed, type AmazonSuiteSpeed } from "@/lib/amazon-suite-speed";
import { selectJobResultAssets } from "@/lib/jobs/results";
import amazonStyles from "./amazon-visual-suite-form.module.css";
import styles from "./product-image-workspace.module.css";

type WorkspaceFeatureId =
  | "reference-remix"
  | "creative-image"
  | "amazon-visual-suite";
type AspectRatio = "follow-reference" | "1:1" | "3:4" | "4:5" | "4:3" | "9:16" | "16:9";
type CreativeDirection =
  | "selling_point"
  | "pain_point"
  | "scenario"
  | "promotion"
  | "premium"
  | "social";
type RoleKey =
  | "product"
  | "reference"
  | "competitor"
  | "whiteBackground"
  | "detail"
  | "package"
  | "scene"
  | "brand";
type JobBody =
  | ReferenceRemixJobCreate
  | CreativeImageJobCreate
  | AmazonVisualSuiteJobCreate;

type RoleDefinition = {
  key: RoleKey;
  title: string;
  description: string;
  required: boolean;
};

type AmazonPlanPage = {
  itemKey: string;
  finalSize?: string;
  headline?: string;
  sellingPoint?: string;
  visualProof?: string;
  layoutDirection?: string;
};

const aspectRatios: { value: AspectRatio; label: string; note: string }[] = [
  { value: "1:1", label: "1:1", note: "1024 × 1024" },
  { value: "3:4", label: "3:4", note: "768 × 1024" },
  { value: "4:5", label: "4:5", note: "1024 × 1280" },
  { value: "4:3", label: "4:3", note: "1024 × 768" },
  { value: "9:16", label: "9:16", note: "576 × 1024" },
  { value: "16:9", label: "16:9", note: "1024 × 576" },
];

const referenceAspectRatios: { value: AspectRatio; label: string; note: string }[] = [
  { value: "follow-reference", label: "跟随参考图", note: "首张参考图画幅比例" },
  ...aspectRatios,
];

const imageCountOptions = Array.from({ length: 12 }, (_, index) => index + 1);
const AMAZON_SOURCE_ASSET_LIMIT = 8;
const AMAZON_PRIMARY_ROLE_KEYS: readonly RoleKey[] = ["whiteBackground"];
const AMAZON_REFERENCE_ROLE_KEYS: readonly RoleKey[] = ["reference"];
const AMAZON_ADVANCED_ROLE_KEYS: readonly RoleKey[] = [
  "detail",
  "package",
  "scene",
  "brand",
  "competitor",
];

const platforms = [
  { value: "tmall", label: "天猫 / 淘宝" },
  { value: "jd", label: "京东" },
  { value: "douyin", label: "抖音电商" },
  { value: "xiaohongshu", label: "小红书" },
  { value: "amazon-us", label: "亚马逊美国站" },
  { value: "generic", label: "通用渠道" },
];

const amazonPlatforms = [
  { value: "amazon-us", label: "亚马逊美国站" },
] as const;

const languageOptions = [
  { value: "zh-CN", label: "简体中文" },
  { value: "en-US", label: "英语（美国）" },
  { value: "ja-JP", label: "日语" },
  { value: "de-DE", label: "德语" },
] as const;

const creativeDirections: { value: CreativeDirection; label: string }[] = [
  { value: "selling_point", label: "核心卖点" },
  { value: "pain_point", label: "用户痛点" },
  { value: "scenario", label: "使用场景" },
  { value: "promotion", label: "活动促销" },
  { value: "premium", label: "高端质感" },
  { value: "social", label: "社媒传播" },
];

const emptySelections: Record<RoleKey, string[]> = {
  product: [],
  reference: [],
  competitor: [],
  whiteBackground: [],
  detail: [],
  package: [],
  scene: [],
  brand: [],
};

function splitList(value: string, maxItems = 12): string[] {
  return value
    .split(/[\n,，;；]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, maxItems);
}

function readAmazonPlanPages(payload: Record<string, unknown> | undefined): AmazonPlanPage[] {
  if (!payload || !Array.isArray(payload.pages)) return [];
  return payload.pages.flatMap((value) => {
    if (!value || typeof value !== "object") return [];
    const page = value as Record<string, unknown>;
    if (typeof page.item_key !== "string" || !page.item_key.trim()) return [];
    const optionalText = (key: string) =>
      typeof page[key] === "string" && page[key].trim() ? page[key].trim() : undefined;
    return [{
      itemKey: page.item_key.trim(),
      finalSize: optionalText("final_size"),
      headline: optionalText("headline"),
      sellingPoint: optionalText("selling_point"),
      visualProof: optionalText("visual_proof"),
      layoutDirection: optionalText("layout_direction"),
    }];
  });
}

type AmazonBusinessQualityStatus =
  | "needs_review"
  | "passed"
  | "failed"
  | "review_infrastructure_failed";

const AMAZON_BUSINESS_QUALITY_COPY: Record<
  AmazonBusinessQualityStatus,
  { title: string; description: string }
> = {
  passed: {
    title: "技术交付与业务审核均已通过",
    description: "已确认商品主档、商品编码一致性和跨页质量。",
  },
  failed: {
    title: "业务质量检查未通过",
    description: "自动检查发现部分图片存在明确质量问题。仅状态为已完成且可交付的图片可下载，请查看失败项并按需重试。",
  },
  needs_review: {
    title: "技术交付完成，业务质量等待人工审核",
    description: "商品编码身份与跨页视觉一致性需要人工确认；文件可下载，重做必须由你明确发起。",
  },
  review_infrastructure_failed: {
    title: "图片已生成，但自动视觉审核基础设施失败",
    description: "已生成图片仍保留并可下载；本次自动审核不可用，不代表图片质量未通过，请人工核对。",
  },
};

function readAmazonBusinessQuality(payload: Record<string, unknown> | undefined) {
  if (!payload || !payload.business_quality || typeof payload.business_quality !== "object") return undefined;
  const quality = payload.business_quality as Record<string, unknown>;
  const status = quality.status;
  if (
    typeof status !== "string" ||
    !Object.prototype.hasOwnProperty.call(AMAZON_BUSINESS_QUALITY_COPY, status)
  ) {
    return undefined;
  }
  return { status: status as AmazonBusinessQualityStatus };
}

function shouldShowAmazonBusinessQuality(
  jobStatus: string | undefined,
  qualityStatus: AmazonBusinessQualityStatus,
) {
  return (
    jobStatus === "completed" ||
    (
      jobStatus === "partial" &&
      (qualityStatus === "failed" || qualityStatus === "review_infrastructure_failed")
    )
  );
}

function isPreservedAmazonReviewInfrastructureBlocker(
  itemStatus: string,
  errorDetails: Record<string, unknown> | null | undefined,
) {
  return (
    itemStatus === "failed" &&
    errorDetails?.failure_kind === "review_infrastructure_failure" &&
    errorDetails.image_quality_failed === false &&
    errorDetails.generated_asset_preserved === true
  );
}

function rolesFor(featureId: WorkspaceFeatureId): RoleDefinition[] {
  if (featureId === "reference-remix") {
    return [
      {
        key: "product",
        title: "我的商品图",
        description: "1 张清晰产品原图，用于锁定外形、材质和关键细节。",
        required: true,
      },
      {
        key: "reference",
        title: "参考图",
        description: "1 张单屏对标图，画幅与构图跟随这张图片。",
        required: true,
      },
    ];
  }
  if (featureId === "creative-image") {
    return [
      {
        key: "product",
        title: "商品图",
        description: "至少 1 张，建议包含正面和关键细节。",
        required: true,
      },
      {
        key: "competitor",
        title: "竞品参考",
        description: "可选，仅用于辅助理解赛道表达，不直接复制。",
        required: false,
      },
    ];
  }
  return [
    {
      key: "whiteBackground",
      title: "商品素材",
      description: "至少上传 1 张清晰商品图，白底图优先。",
      required: true,
    },
    {
      key: "detail",
      title: "商品细节图",
      description: "可选，用于材质、结构和功能特写。",
      required: false,
    },
    {
      key: "package",
      title: "包装参考",
      description: "可选，用于包装、配件和清单表达。",
      required: false,
    },
    {
      key: "scene",
      title: "场景参考",
      description: "可选，用于使用场景和生活方式画面。",
      required: false,
    },
    {
      key: "brand",
      title: "品牌参考",
      description: "可选，用于品牌标志、品牌色和视觉规范。",
      required: false,
    },
    {
      key: "reference",
      title: "参考素材",
      description: "有场景、风格或品牌参考时再上传。",
      required: false,
    },
    {
      key: "competitor",
      title: "竞品参考",
      description: "可选，用于理解类目表达和差异化机会，不直接复制。",
      required: false,
    },
  ];
}

function UploadPanel({
  title, description, count, required, uploading, disabled, maxFiles, onFiles, onOpenLibrary,
}: {
  title: string;
  description: string;
  count: number;
  required: boolean;
  uploading: boolean;
  disabled: boolean;
  maxFiles: number;
  onFiles: (files: File[]) => Promise<void>;
  onOpenLibrary?: () => void;
}) {
  return (
    <ImageDropZone label={title} className={styles.uploadPanel} multiple maxFiles={maxFiles}
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
          <button type="button" onClick={openFileDialog} disabled={uploadDisabled}>
            {uploadingFile ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}
            上传图片
          </button>
          {onOpenLibrary && <button type="button" data-library aria-haspopup="dialog"
            onClick={onOpenLibrary} disabled={disabled || uploading}>
            <Images size={16} />从素材库选择
          </button>}
        </div>
      </>}
    </ImageDropZone>
  );
}

export function CommerceSkillWorkspace({
  featureId,
}: {
  featureId: WorkspaceFeatureId;
}) {
  const feature = getFeatureById(featureId)!;
  const roleDefinitions = useMemo(() => rolesFor(featureId), [featureId]);
  const { workspace, isPending: workspacePending, error: workspaceError } = useWorkspace();
  const upload = useUploadAsset(workspace?.id);
  const runtime = useRuntimeStatus();
  const capability = resolveRuntimeCapability(
    runtime.data,
    feature?.runtimeCapability ?? featureId,
  );
  const workflow = presentWorkflow(capability);
  const createJob = useCreateJob(workspace?.id);
  const confirmPlan = useConfirmPlan(workspace?.id);

  const [jobId, setJobId] = useWorkspaceJobId(feature.route);
  const job = useJob(workspace?.id, jobId);
  const saved = workspaceJobSnapshot(job.data);

  const [draftSelections, setSelections] = useState(emptySelections);
  const [draftPlatformCode, setPlatformCode] = useState(
    featureId === "amazon-visual-suite" ? "amazon-us" : "tmall",
  );
  const [draftLanguage, setLanguage] = useState(
    featureId === "amazon-visual-suite" ? "en-US" : "zh-CN",
  );
  const [draftAspectRatio, setAspectRatio] = useState<AspectRatio>(
    featureId === "reference-remix" ? "follow-reference" : "1:1",
  );
  const [draftImageCount, setImageCount] = useState(1);
  const [draftCreativeDirection, setCreativeDirection] =
    useState<CreativeDirection>("selling_point");
  const [draftInstruction, setInstruction] = useState("");
  const [draftSellingPoints, setSellingPoints] = useState("");
  const [draftProductName, setProductName] = useState("");
  const [draftAmazonSpeed, setAmazonSpeed] = useState<AmazonSuiteSpeed>("standard");
  const hydratedAmazonSpeedWorkspace = useRef<string | undefined>(undefined);
  // Keep the full backend contract while the simplified Amazon form leaves legacy optional fields blank.
  const category = "";
  const targetBuyer = "";
  const listingTitle = "";
  const listingDescription = "";
  const aPlusCopy = "";
  const [draftProductFacts, setProductFacts] = useState("");
  const parameters = "";
  const certificationsOrEvidence = "";
  const forbiddenClaims = "";
  const brandColors = "";
  const stylePreferences = "";
  const [uiError, setUiError] = useState<string>();
  const [humanReviewComplete, setHumanReviewComplete] = useWorkspaceJobReview(jobId);
  const [libraryRole, setLibraryRole] = useState<RoleKey>();
  const [autoStart, setAutoStart] = useState(false);
  const [uploadingSources, setUploadingSources] = useState(false);
  const uploadBatchLock = useRef(false);
  const uploadBusy = uploadingSources || upload.isPending;

  const selections = jobId ? {
    product: saved.ids("product_asset_ids"), reference: saved.ids("reference_asset_ids"),
    competitor: saved.ids(featureId === "amazon-visual-suite" ? "competitor_reference_asset_ids" : "competitor_asset_ids"),
    whiteBackground: saved.ids("white_background_product_asset_ids"), detail: saved.ids("detail_asset_ids"),
    package: saved.ids("package_asset_ids"), scene: saved.ids("scene_asset_ids"), brand: saved.ids("brand_asset_ids"),
  } : draftSelections;
  const platformCode = jobId ? saved.text(featureId === "amazon-visual-suite" ? "marketplace" : "platform_code") : draftPlatformCode;
  const language = jobId ? saved.text("language") : draftLanguage;
  const aspectRatio = jobId ? saved.choice("aspect_ratio", referenceAspectRatios.map((ratio) => ratio.value), "1:1") : draftAspectRatio;
  const imageCount = jobId ? saved.number("image_count", job.data?.expected_item_count ?? 1) : draftImageCount;
  const creativeDirection = jobId ? saved.choice("creative_direction", creativeDirections.map((direction) => direction.value), "selling_point") : draftCreativeDirection;
  const instruction = jobId ? saved.text("instruction") : draftInstruction;
  const sellingPoints = jobId ? saved.lines(featureId === "amazon-visual-suite" ? "bullet_points" : "selling_points") : draftSellingPoints;
  const productName = jobId ? saved.text("product_name") : draftProductName;
  const amazonSpeed = jobId ? readAmazonSuiteSpeed(saved.text("amazon_speed")) : draftAmazonSpeed;
  const productFacts = jobId ? [saved.lines("bullet_points"), saved.lines("product_facts")].filter(Boolean).join("\n") : draftProductFacts;

  useEffect(() => {
    if (featureId !== "amazon-visual-suite" || !workspace?.id || jobId || hydratedAmazonSpeedWorkspace.current === workspace.id) return;
    hydratedAmazonSpeedWorkspace.current = workspace.id;
    try {
      // Restore this workspace's explicit draft choice; new workspaces start with standard.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setAmazonSpeed(readAmazonSuiteSpeed(window.localStorage.getItem(amazonSuiteSpeedDraftKey(workspace.id))));
    } catch {
      // Storage may be disabled; the default version remains usable.
    }
  }, [featureId, workspace?.id, jobId]);

  const selectAmazonSpeed = (speed: AmazonSuiteSpeed) => {
    if (jobId) return;
    setAmazonSpeed(speed);
    if (workspace?.id) {
      try {
        window.localStorage.setItem(amazonSuiteSpeedDraftKey(workspace.id), speed);
      } catch {
        // An unavailable draft store must not block task submission.
      }
    }
  };

  useEffect(() => {
    if (jobId || typeof window === "undefined") return;
    try {
      if (featureId !== "reference-remix") {
        const rawCompose = window.localStorage.getItem("polaris-inspiration-compose");
        if (rawCompose) {
          const compose = JSON.parse(rawCompose) as { instruction?: string };
          if (compose.instruction) {
            // Hydrate the one-time handoff payload into the existing composer.
            // eslint-disable-next-line react-hooks/set-state-in-effect
            setInstruction((current) => current || compose.instruction!);
          }
          window.localStorage.removeItem("polaris-inspiration-compose");
        }
        return;
      }
      const raw = window.localStorage.getItem("polaris-inspiration-strategy");
      if (!raw) return;
      const strategy = JSON.parse(raw) as { visualStrategyLock?: string; ratio?: AspectRatio; intent?: string; productAssetIds?: string[]; referenceAssetIds?: string[]; autoStart?: boolean };
      if (strategy.productAssetIds?.length || strategy.referenceAssetIds?.length) {
        setSelections((current) => ({
          ...current,
          product: strategy.productAssetIds ?? [],
          reference: strategy.referenceAssetIds ?? [],
        }));
      }
      if (strategy.intent || strategy.visualStrategyLock) {
        const handoffInstruction = [
          strategy.intent,
          strategy.visualStrategyLock ? `灵感策略（已锁定，仅迁移视觉关系）：${strategy.visualStrategyLock}` : "",
        ].filter(Boolean).join("\n\n");
        setInstruction((current) => current || handoffInstruction);
      }

      if (strategy.autoStart) setAutoStart(true);
      window.localStorage.removeItem("polaris-inspiration-strategy");
    } catch {
      // Ignore malformed handoff data and keep the normal workspace flow intact.
    }
  }, [featureId, jobId]);

  const resultAssets = selectJobResultAssets(job.data);
  const requestError = workspaceError ?? upload.error ?? confirmPlan.error ?? job.error;
  const isAmazon = featureId === "amazon-visual-suite";
  const isCreative = featureId === "creative-image";
  const primaryRoleDefinitions = isAmazon
    ? roleDefinitions.filter((role) => AMAZON_PRIMARY_ROLE_KEYS.includes(role.key))
    : roleDefinitions;
  const referenceRoleDefinitions = isAmazon
    ? roleDefinitions.filter((role) => AMAZON_REFERENCE_ROLE_KEYS.includes(role.key))
    : [];
  const advancedRoleDefinitions = isAmazon
    ? roleDefinitions.filter((role) => AMAZON_ADVANCED_ROLE_KEYS.includes(role.key))
    : [];
  const activeLibraryRole = roleDefinitions.find((role) => role.key === libraryRole);
  const selectedPlatformLabel = (isAmazon ? amazonPlatforms : platforms).find(
    (platform) => platform.value === platformCode,
  )?.label ?? "未选择站点";
  const selectedLanguageLabel = languageOptions.find((option) => option.value === language)?.label
    ?? "未选择语言";
  const selectedSizeLabel = isAmazon
    ? `${amazonSpeed === "fast" ? "目标画幅：" : ""}主图 2000 × 2000；副图 1200 × 1600；桌面 A+ 1464 × 600；移动 A+ 1600 × 1200`
    : aspectRatio === "follow-reference"
      ? "跟随首张参考图画幅比例"
      : aspectRatios.find((ratio) => ratio.value === aspectRatio)?.note ?? "未选择";
  const expectedCount = isAmazon
    ? 21
    : job.data?.expected_item_count ?? imageCount;
  const amazonPlanPages = isAmazon
    ? readAmazonPlanPages(job.data?.plan?.payload)
    : [];
  const amazonBusinessQuality = isAmazon
    ? readAmazonBusinessQuality(job.data?.plan?.payload)
    : undefined;
  const amazonBusinessQualityCopy = amazonBusinessQuality
    ? AMAZON_BUSINESS_QUALITY_COPY[amazonBusinessQuality.status]
    : undefined;
  const showAmazonBusinessQuality = Boolean(
    isAmazon &&
      amazonBusinessQuality &&
      shouldShowAmazonBusinessQuality(job.data?.status, amazonBusinessQuality.status),
  );
  const isAmazonReviewInfrastructureFailure = Boolean(
    isAmazon &&
      job.data?.status === "partial" &&
      amazonBusinessQuality?.status === "review_infrastructure_failed" &&
      job.data.items.some((item) =>
        isPreservedAmazonReviewInfrastructureBlocker(
          item.status,
          item.error_details,
        ),
      ),
  );
  const automaticAmazonPlan = isAmazon && Boolean(job.data?.configuration.automatic_amazon_submission);
  const canConfirmAmazonPlan = Boolean(
    isAmazon &&
      !automaticAmazonPlan &&
      job.data?.status === "awaiting_confirmation" &&
      job.data.plan &&
      amazonPlanPages.length > 0 &&
      allowsJobAction(job.data.allowed_actions, "confirm_plan"),
  );

  const selectedIds = Object.values(selections).flat();
  const amazonRemainingSourceSlots = isAmazon
    ? Math.max(0, AMAZON_SOURCE_ASSET_LIMIT - selectedIds.length)
    : undefined;
  const canDownloadAmazonArchive = Boolean(
    isAmazon &&
      resultAssets.length === expectedCount &&
      (job.data?.status === "completed" || isAmazonReviewInfrastructureFailure),
  );
  const missingRequiredRole = roleDefinitions.find(
    (role) => role.required && selections[role.key].length === 0,
  );
  const canSubmit = Boolean(
    !jobId && workspace &&
      workflow.canSubmit &&
      !runtime.isPending &&
      !missingRequiredRole &&
      (featureId !== "reference-remix" || splitList(sellingPoints).length > 0) &&
      !uploadBusy &&
      !createJob.isPending,
  );

  const progressText = useMemo(() => {
    if (!job.data) return "任务创建后将自动轮询状态";
    if (automaticAmazonPlan && job.data.status === "planning") {
      return "正在规划，完成校验后将自动生成 21 张图片";
    }
    if (isAmazonReviewInfrastructureFailure) {
      const generatedCount = resultAssets.length;
      return generatedCount === expectedCount
        ? `${generatedCount} 张图片已生成，自动视觉审核未完成`
        : `${generatedCount} / ${expectedCount} 张图片已生成，自动视觉审核未完成`;
    }
    return `${job.data.succeeded_item_count} / ${job.data.expected_item_count} 项完成`;
  }, [automaticAmazonPlan, expectedCount, isAmazonReviewInfrastructureFailure, job.data, resultAssets.length]);

  const replaceRoleSelection = (role: RoleKey, ids: string[]) => {
    const requested = [...new Set(ids)].slice(0, AMAZON_SOURCE_ASSET_LIMIT);
    setSelections((current) => {
      const next = { ...current };
      for (const key of Object.keys(next) as RoleKey[]) {
        if (key !== role) {
          next[key] = next[key].filter((id) => !requested.includes(id));
        }
      }
      const otherRoleCount = Object.entries(next).reduce(
        (count, [key, assetIds]) => count + (key === role ? 0 : assetIds.length),
        0,
      );
      const roleLimit = isAmazon
        ? Math.max(0, AMAZON_SOURCE_ASSET_LIMIT - otherRoleCount)
        : featureId === "reference-remix" ? 1 : AMAZON_SOURCE_ASSET_LIMIT;
      next[role] = requested.slice(0, roleLimit);
      return next;
    });
  };

  const addUploadedAssets = async (files: File[], role: RoleKey) => {
    if (!workspace?.id || jobId || createJob.isPending || uploadBatchLock.current) return;
    setUiError(undefined);
    const availableSlots = isAmazon
      ? Math.max(0, AMAZON_SOURCE_ASSET_LIMIT - selectedIds.length)
      : Math.max(0, (featureId === "reference-remix" ? 1 : AMAZON_SOURCE_ASSET_LIMIT) - selections[role].length);
    const selected = validateImageUpload(files, { multiple: true, maxFiles: availableSlots });
    if (selected.error) {
      setUiError(selected.error);
      return;
    }
    if (!selected.files.length) return;
    uploadBatchLock.current = true;
    setUploadingSources(true);
    try {
      const uploadedIds: string[] = [];
      const failures: string[] = [];
      for (const file of selected.files) {
        try {
          const asset = await upload.mutateAsync({ file, mediaKind: "image" });
          uploadedIds.push(asset.id);
        } catch (error) {
          failures.push(`${file.name || "所选图片"}：${errorMessage(error)}`);
        }
      }
      if (uploadedIds.length) {
        replaceRoleSelection(role, [...selections[role], ...uploadedIds]);
      }
      if (failures.length) {
        const uploadedNote = uploadedIds.length ? `已成功上传 ${uploadedIds.length} 张；` : "";
        const remaining = failures.length > 1 ? `，另有 ${failures.length - 1} 张上传失败` : "";
        setUiError(`${uploadedNote}${failures[0]}${remaining}`);
      }
    } finally {
      uploadBatchLock.current = false;
      setUploadingSources(false);
    }
  };

  const buildJobBody = (): JobBody => {
    const common = {
      intent: "submit" as const,
      display_name: feature.title,
    };
    if (featureId === "reference-remix") {
      return {
        ...common,
        type: "reference_remix",
        input: {
          product_asset_ids: selections.product,
          reference_asset_ids: selections.reference,
        },
        configuration: {
          reference_skill: "aurora-remix-fast" as const,
          platform_code: platformCode,
          language: "zh-CN",
          selling_points: splitList(sellingPoints),
          aspect_ratio: "follow-reference",
          image_count: imageCount,
          execution_mode: "planner",
          inspiration_product_first: true,
          quality: "high",
          output_format: "png",
          instruction: instruction.trim(),
          product_identity_lock:
            "Product image is the only identity authority. Preserve product geometry, materials, packaging, visible brand and text exactly; do not invent or rewrite product text.",
          reference_visual_analysis:
            "Analyze the reference image for canvas, crop, layout, subject positions, safe areas, camera, lighting, typography zones, and commercial intent. Transfer relationships and visual grammar only.",
          commercial_intent:
            "Create an original ecommerce advertisement that makes the user product the hero and preserves believable product-person contact where present.",
          reusable_visual_strategy:
            "Reuse composition, scale hierarchy, spatial relations, camera, lighting, and commercial roles as abstract structure, never source nouns or proprietary assets.",
          original_remix_prompt: instruction.trim(),
          forbidden_transfer:
            "Do not transfer source branding, logos, watermarks, exact copy, proprietary assets, unsupported claims, or hidden prompts.",
          post_generation_review:
            "Review product identity, visible product text, visual strategy, role relationships, and leakage before accepting the result.",
        },
      };
    }
    if (featureId === "creative-image") {
      return {
        ...common,
        type: "creative_image",
        input: {
          product_asset_ids: selections.product,
          competitor_asset_ids: selections.competitor,
        },
        configuration: {
          creative_direction: creativeDirection,
          platform_code: platformCode,
          language,
          selling_points: splitList(sellingPoints),
          aspect_ratio: aspectRatio as Exclude<AspectRatio, "follow-reference">,
          image_count: imageCount,
          inspiration_planning: false,
          // A competitor reference is a visual-strategy input. Keep the
          // standalone workspace on the Skill's reference-planning path so
          // the provider runs the inspiration analysis stage before edit.
          inspiration_reference_planning: selections.competitor.length > 0,
          instruction: instruction.trim(),
        },
      };
    }
    return {
      ...common,
      type: "amazon_visual_suite",
      input: {
        white_background_product_asset_ids: selections.whiteBackground,
        detail_asset_ids: selections.detail,
        package_asset_ids: selections.package,
        scene_asset_ids: selections.scene,
        brand_asset_ids: selections.brand,
        reference_asset_ids: selections.reference,
        competitor_reference_asset_ids: selections.competitor,
      },
      configuration: {
        auto_start_after_plan: true,
        ...amazonSuiteSpeedConfiguration(amazonSpeed),
        marketplace:
          amazonPlatforms.find((platform) => platform.value === platformCode)?.value ??
          amazonPlatforms[0].value,
        product_name: productName.trim(),
        category: category.trim(),
        target_buyer: targetBuyer.trim(),
        listing_title: listingTitle.trim(),
        bullet_points: splitList(sellingPoints),
        listing_description: listingDescription.trim(),
        a_plus_copy: aPlusCopy.trim(),
        product_facts: splitList(productFacts, 24),
        parameters: splitList(parameters, 24),
        certifications_or_evidence: splitList(certificationsOrEvidence, 24),
        forbidden_claims: splitList(forbiddenClaims, 24),
        brand_colors: splitList(brandColors, 8),
        style_preferences: splitList(stylePreferences, 12),
        instruction: instruction.trim(),
        language,
      },
    };
  };

  const submit = async () => {
    if (!canSubmit || uploadBatchLock.current) return;
    setUiError(undefined);
    try {
      const created = await createJob.mutateAsync(buildJobBody());
      setHumanReviewComplete(false);
      setJobId(created.id);
    } catch (error) {
      setUiError(errorMessage(error));
    }
  };

  useEffect(() => {
    if (!autoStart || !canSubmit || jobId) return;
    // Auto-submit is an intentional one-shot side effect after handoff state
    // has hydrated; keep the existing workflow ordering intact.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAutoStart(false);
    void submit();
    // submit is stable for this render and intentionally excluded to avoid a
    // second auto-submit when unrelated form state changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart, canSubmit, jobId]);

  const resetTask = () => {
    setJobId(undefined);
    setUiError(undefined);
    setHumanReviewComplete(false);
    createJob.reset();
  };

  return (
    <WorkspaceJobGate jobId={jobId} job={job.data} error={job.error} onRetry={() => void job.refetch()} expectedType={feature.jobType}>
    <AppShell flush sidebar={false}>
      <div className={`${styles.workspacePage} ${isAmazon ? amazonStyles.amazonWorkspace : ""}`}>
        <WorkspaceHeader
          jobId={jobId}
          eyebrow={`${feature.shortTitle} · 独立工作台`}
          title={feature.title}
          description={feature.description}
          meta={
            <span className={styles.safetyBadge}>
              <ShieldCheck size={15} /> 任务进度自动保存
            </span>
          }
        />

        <div className={`${styles.stepBar} ${isAmazon ? amazonStyles.amazonStepBar : ""}`} aria-label="创作步骤">
          <span data-active={(isAmazon ? !jobId && selections.whiteBackground.length === 0 : !jobId) || undefined}><i>1</i>{isAmazon ? "上传图片" : "选择素材"}</span>
          <span data-active={(isAmazon ? !jobId && selections.whiteBackground.length > 0 : !jobId) || undefined}><i>2</i>{isAmazon ? "填写卖点" : "配置参数"}</span>
          <span data-active={Boolean(jobId) || undefined}><i>3</i>生成进度</span>
          <span data-active={resultAssets.length > 0 || undefined}><i>4</i>结果交付</span>
        </div>

        <JobProgressFeedback pending={createJob.isPending && !jobId} />

        <div className={styles.workspaceGrid} data-single-column>
          <main className={styles.mainColumn}>
            <section className={styles.card}>
              <div className={styles.sectionHeading}>
                <div>
                  <span>01</span>
                  <div>
                    <h2>{isAmazon ? "上传商品素材" : "选择角色素材"}</h2>
                    <p>
                      {isAmazon
                        ? `至少上传 1 张清晰商品图，素材合计最多 ${AMAZON_SOURCE_ASSET_LIMIT} 张。`
                        : featureId === "reference-remix" ? "每个角色各 1 张，同一素材不能跨角色重复使用。"
                        : `同一素材不能跨角色重复选择，每个角色最多 ${AMAZON_SOURCE_ASSET_LIMIT} 张。`}
                    </p>
                  </div>
                </div>
                <strong>
                  {isAmazon
                    ? `已选 ${selectedIds.length} / ${AMAZON_SOURCE_ASSET_LIMIT} 张`
                    : `${selectedIds.length} 张`}
                </strong>
              </div>

              <div className={`${styles.uploadGrid} ${isAmazon ? amazonStyles.primaryUploadGrid : ""}`}>
                {primaryRoleDefinitions.map((role) => (
                  <UploadPanel
                    key={role.key}
                    title={role.title}
                    description={role.description}
                    count={selections[role.key].length}
                    required={role.required}
                    uploading={uploadBusy}
                    disabled={Boolean(jobId) || !workspace?.id || createJob.isPending}
                    maxFiles={amazonRemainingSourceSlots ?? Math.max(0, (featureId === "reference-remix" ? 1 : AMAZON_SOURCE_ASSET_LIMIT) - selections[role.key].length)}
                    onFiles={(files) => addUploadedAssets(files, role.key)}
                    onOpenLibrary={() => setLibraryRole(role.key)}
                  />
                ))}
              </div>

              {isAmazon ? (
                <div className={amazonStyles.optionalSections}>
                  <details className={amazonStyles.advancedSection}>
                    <summary>
                      <span>添加参考图片</span>
                      <small>有场景、风格或品牌参考时再添加</small>
                    </summary>
                    <div className={amazonStyles.advancedSectionBody}>
                      <div className={amazonStyles.primaryUploadGrid}>
                        {referenceRoleDefinitions.map((role) => (
                          <UploadPanel
                            key={role.key}
                            title={role.title}
                            description={role.description}
                            count={selections[role.key].length}
                            required={role.required}
                            uploading={uploadBusy}
                            disabled={Boolean(jobId) || !workspace?.id || createJob.isPending}
                            maxFiles={amazonRemainingSourceSlots ?? 0}
                            onFiles={(files) => addUploadedAssets(files, role.key)}
                            onOpenLibrary={() => setLibraryRole(role.key)}
                          />
                        ))}
                      </div>
                    </div>
                  </details>

                  <details className={amazonStyles.advancedSection}>
                    <summary>
                      <span>按用途分类素材</span>
                      <small>细节、包装、场景、品牌或竞品</small>
                    </summary>
                    <div className={amazonStyles.advancedSectionBody}>
                      <div className={styles.uploadGrid}>
                        {advancedRoleDefinitions.map((role) => (
                          <UploadPanel
                            key={role.key}
                            title={role.title}
                            description={role.description}
                            count={selections[role.key].length}
                            required={role.required}
                            uploading={uploadBusy}
                            disabled={Boolean(jobId) || !workspace?.id || createJob.isPending}
                            maxFiles={amazonRemainingSourceSlots ?? 0}
                            onFiles={(files) => addUploadedAssets(files, role.key)}
                            onOpenLibrary={() => setLibraryRole(role.key)}
                          />
                        ))}
                      </div>
                    </div>
                  </details>
                </div>
              ) : null}
              {jobId && <WorkspaceJobInputs workspaceId={workspace?.id} job={job.data} />}
            </section>
            <section className={styles.card}>
              <div className={styles.sectionHeading}>
                <div>
                  <span>02</span>
                  <div>
                    <h2>{isAmazon ? "告诉我们这个商品" : "配置生成任务"}</h2>
                    <p>
                      {isAmazon
                        ? "除商品图片外，其余信息都可以跳过。"
                        : "设置平台、比例和生成数量，每张结果均为独立图片。"}
                    </p>
                  </div>
                </div>
                <strong>{isAmazon ? `${selectedPlatformLabel} · ${selectedLanguageLabel}` : `${imageCount} 项`}</strong>
              </div>

              {isAmazon ? (
                <div className={amazonStyles.amazonForm}>
                  <fieldset className={amazonStyles.speedOptions} disabled={Boolean(jobId)}>
                    <legend>生成版本</legend>
                    <div className={amazonStyles.speedChoices}>
                      {([
                        ["fast", "快速版（约5分钟）"],
                        ["standard", "精致版（约40分钟）"],
                      ] as const).map(([value, title]) => (
                        <label key={value} data-selected={amazonSpeed === value || undefined}>
                          <input type="radio" name="amazon-suite-speed" value={value} checked={amazonSpeed === value} onChange={() => selectAmazonSpeed(value)} />
                          <span><strong>{title}{value === "fast" && <GenerationRecommendation />}</strong></span>
                        </label>
                      ))}
                    </div>
                    <p>生成 21 张图片，覆盖主图、副图及桌面与移动 A+。</p>
                  </fieldset>
                  <label className={amazonStyles.simpleField}>
                    <span>商品名称</span>
                    <input
                      value={productName}
                      onChange={(event) => setProductName(event.target.value)}
                      maxLength={160}
                      placeholder="例如：旅行背包"
                      disabled={Boolean(jobId)}
                    />
                  </label>

                  <label className={amazonStyles.simpleField}>
                    <span>商品卖点与参数</span>
                    <textarea
                      value={productFacts}
                      onChange={(event) => setProductFacts(event.target.value)}
                      rows={4}
                      placeholder={"每行写一项，也可以直接粘贴已有商品文案，例如：\n轻便耐磨\n可放 15.6 英寸电脑\n尺寸 42 × 28 × 18 厘米"}
                      disabled={Boolean(jobId)}
                    />
                  </label>

                  <label className={amazonStyles.simpleField}>
                    <span>品牌与生成要求</span>
                    <textarea
                      value={instruction}
                      onChange={(event) => setInstruction(event.target.value)}
                      rows={4}
                      maxLength={4000}
                      placeholder="例如：品牌色为深蓝；面向通勤人群；优先突出收纳；不要出现具体防水等级"
                      disabled={Boolean(jobId)}
                    />
                  </label>
                </div>
              ) : (
                <>
                  <div className={styles.formGrid}>
                    <label>
                      <span>目标平台</span>
                      <ReplicaSelect label="目标平台" value={platformCode} onChange={setPlatformCode} disabled={Boolean(jobId)} options={platforms.map(platform => [platform.value, platform.label] as const)} />
                      <small>用于匹配不同渠道的构图与视觉规范。图片尺寸：{selectedSizeLabel}</small>
                    </label>
                    <label>
                      <span>图片文案语言</span>
                      <ReplicaSelect label="图片文案语言" value={language} onChange={setLanguage} disabled={Boolean(jobId)} options={(featureId === "reference-remix" && !jobId ? languageOptions.filter(option => option.value === "zh-CN") : languageOptions).map(option => [option.value, option.label] as const)} />
                      <small>用于生成图片中的文字内容。</small>
                    </label>
                    {isCreative ? (
                      <label>
                        <span>创意方向</span>
                        <ReplicaSelect label="创意方向" value={creativeDirection} onChange={value => setCreativeDirection(value as CreativeDirection)} disabled={Boolean(jobId)} options={creativeDirections.map(direction => [direction.value, direction.label] as const)} />
                        <small>用于确定画面的主要表达方向。</small>
                      </label>
                    ) : null}
                  </div>

                  <fieldset className={styles.optionGroup} disabled={Boolean(jobId)}>
                    <legend>画面比例</legend>
                    <div className={styles.ratioGrid}>
                      {(featureId === "reference-remix" && !jobId ? referenceAspectRatios.filter(ratio => ratio.value === "follow-reference") : featureId === "reference-remix" ? referenceAspectRatios : aspectRatios).map((ratio) => (
                        <label key={ratio.value} data-selected={aspectRatio === ratio.value || undefined}>
                          <input
                            type="radio"
                            name={`${featureId}-aspect-ratio`}
                            value={ratio.value}
                            checked={aspectRatio === ratio.value}
                            onChange={() => setAspectRatio(ratio.value)}
                          />
                          <span className={styles.ratioPreview} aria-hidden="true"><i style={{ aspectRatio: ratio.value === "follow-reference" ? "3 / 4" : ratio.value.replace(":", " / ") }} /></span>
                          <strong>{ratio.label}</strong>
                          <small>{ratio.note}</small>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <fieldset className={styles.optionGroup} disabled={Boolean(jobId)}>
                    <legend>生成数量</legend>
                    <div className={styles.countOptions}>
                      {imageCountOptions.map((count) => (
                        <label key={count} data-selected={imageCount === count || undefined}>
                          <input
                            type="radio"
                            name={`${featureId}-image-count`}
                            value={count}
                            checked={imageCount === count}
                            onChange={() => setImageCount(count)}
                          />
                          <strong>{count}</strong><span>张</span>
                        </label>
                      ))}
                    </div>
                  </fieldset>

                  <label className={styles.instructionField}>
                    <span>{featureId === "reference-remix" ? "商品事实与不可变特征 *" : "商品卖点"}</span>
                    <textarea
                      value={sellingPoints}
                      onChange={(event) => setSellingPoints(event.target.value)}
                      rows={4}
                      placeholder={featureId === "reference-remix" ? "请填写真实商品事实、结构特征或纹样方向，每行一项" : "每行一项，最多 12 项"}
                      disabled={Boolean(jobId)}
                    />
                    <small>按换行或逗号拆分，最多提交 12 项。</small>
                  </label>

                  <label className={styles.instructionField}>
                    <span>补充要求</span>
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
                </>
              )}
            </section>

            {jobId ? (
              <section className={styles.card}>
                <div className={styles.sectionHeading}>
                  <div>
                    <span>03</span>
                    <div><h2>任务状态</h2><p>阶段与完成数量会自动更新。</p></div>
                  </div>
                  {job.data ? <StatusBadge status={job.data.status} /> : null}
                </div>
                {job.isPending ? (
                  <div className={styles.jobState}><LoaderCircle className="spin" size={18} />正在读取任务…</div>
                ) : job.isError && !job.data ? (
                  <div className={styles.jobError} role="alert">{errorMessage(job.error)}</div>
                ) : job.data ? (
                  <div className={styles.jobPanel}>
                    <JobProgressFeedback job={job.data} error={job.error} lastSyncedAt={job.dataUpdatedAt}
                      refreshing={job.isFetching} onRefresh={() => void job.refetch()}
                      summary={isAmazonReviewInfrastructureFailure ? progressText : undefined} suppressFailureDetails={isAmazonReviewInfrastructureFailure} />
                    <div className={styles.itemGrid}>
                      {job.data.items.map((item) => {
                        const isPreservedReviewBlocker =
                          isAmazonReviewInfrastructureFailure &&
                          isPreservedAmazonReviewInfrastructureBlocker(
                            item.status,
                            item.error_details,
                          );
                        return (
                          <span
                            key={item.id}
                            data-status={isPreservedReviewBlocker ? "succeeded" : item.status}
                          >
                            <i /> {item.item_key}
                            {isPreservedReviewBlocker
                              ? " · 图片已生成，自动视觉审核未完成"
                              : null}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </section>
            ) : null}

            {isAmazon && !automaticAmazonPlan && job.data?.status === "awaiting_confirmation" && amazonPlanPages.length > 0 ? (
              <section className={styles.card} aria-labelledby="amazon-plan-review-title">
                <div className={styles.sectionHeading}>
                  <div>
                    <span>04</span>
                    <div>
                      <h2 id="amazon-plan-review-title">确认 21 项页面规划</h2>
                      <p>生成开始前，请逐项核对尺寸、卖点、视觉证明与布局方向。</p>
                    </div>
                  </div>
                  <strong>{amazonPlanPages.length} / 21 项</strong>
                </div>
                <div className={styles.planReview}>
                  {amazonPlanPages.map((page, index) => (
                    <article key={`${page.itemKey}-${index}`}>
                      <i>{String(index + 1).padStart(2, "0")}</i>
                      <div>
                        <div className={styles.planRowHeading}>
                          <strong>{page.itemKey}</strong>
                          {page.finalSize ? <span>{page.finalSize}</span> : null}
                        </div>
                        <h3>{page.headline ?? page.sellingPoint ?? "待核对页面规划"}</h3>
                        {page.sellingPoint && page.sellingPoint !== page.headline ? (
                          <p><b>卖点</b>{page.sellingPoint}</p>
                        ) : null}
                        {page.visualProof ? <p><b>视觉证明</b>{page.visualProof}</p> : null}
                        {page.layoutDirection ? <p><b>布局</b>{page.layoutDirection}</p> : null}
                      </div>
                    </article>
                  ))}
                </div>
                <div className={styles.planConfirmation}>
                  <p><Info size={15} />确认后任务将继续处理并进入整套图片生成，请在提交前核对方案。</p>
                  <button
                    type="button"
                    className={styles.primaryAction}
                    disabled={!canConfirmAmazonPlan || confirmPlan.isPending}
                    onClick={() => {
                      if (!job.data?.plan) return;
                      confirmPlan.mutate({
                        jobId: job.data.id,
                        revision: job.data.plan.revision,
                        allowedActions: job.data.allowed_actions,
                      });
                    }}
                  >
                    {confirmPlan.isPending ? <LoaderCircle className="spin" size={17} /> : <CheckCircle2 size={17} />}
                    {confirmPlan.isPending ? "正在确认计划" : "确认计划并开始生成"}
                    <ArrowRight size={17} />
                  </button>
                </div>
              </section>
            ) : null}

            {showAmazonBusinessQuality && amazonBusinessQuality && amazonBusinessQualityCopy ? (
              <section
                className={styles.businessQuality}
                data-status={amazonBusinessQuality.status}
                aria-live="polite"
              >
                {amazonBusinessQuality.status === "review_infrastructure_failed" ? (
                  <Info size={19} />
                ) : (
                  <ShieldCheck size={19} />
                )}
                <div>
                  <strong>{amazonBusinessQualityCopy.title}</strong>
                  <p>{amazonBusinessQualityCopy.description}</p>
                </div>
              </section>
            ) : null}

            <ResultGrid
              workspaceId={workspace?.id}
              assets={resultAssets}
              expectedCount={expectedCount}
              title={`${feature.title}结果`}
              archiveJobId={canDownloadAmazonArchive ? job.data?.id : undefined}
            />
            {!isAmazon && job.data?.status === "completed" && resultAssets.length > 0 ? (
              <SkillReviewChecklist key={jobId}
                items={isCreative
                  ? [
                      { key: "fidelity", label: "产品外观、颜色、包装、Logo、结构和 SKU 关键细节保持保真。" },
                      { key: "ctr", label: "3 秒和移动端缩略图能看懂卖什么、解决什么问题、为什么值得点击。" },
                      { key: "copy", label: "标题、标签和卖点清晰可读，没有乱码、密集小字或遮挡主体。" },
                      { key: "compliance", label: "没有虚构销量、评价、认证、功效、授权，且没有照搬竞品品牌或文案。" },
                    ]
                  : [
                      { key: "identity", label: "商品身份、轮廓、颜色、材质、包装、Logo、文字和配件与原图一致。" },
                      { key: "strategy", label: "参考图的构图、主体比例、层级、光线和商业角色被原创迁移，没有照搬来源资产。" },
                      { key: "roles", label: "商品角色、支撑/接触关系、文字区和前后景层级完整，没有多余产品角色。" },
                      { key: "leakage", label: "没有源品牌、Logo、水印、原文案、未授权数字或不实声明泄漏。" },
                    ]}
                description={
                  humanReviewComplete
                    ? "已完成当前 Skill 的人工检查；如发现硬性失败，请从原始素材发起一次有界修复。"
                    : undefined
                }
                onCompleteChange={setHumanReviewComplete}
              />
            ) : null}
          </main>

        </div>

        {activeLibraryRole ? (
          <AssetLibraryDialog
            key={activeLibraryRole.key}
            workspaceId={workspace?.id}
            title={`选择${activeLibraryRole.title}`}
            description={activeLibraryRole.description}
            selectedIds={selections[activeLibraryRole.key]}
            excludedIds={selectedIds.filter(
              (id) => !selections[activeLibraryRole.key].includes(id),
            )}
            onClose={() => setLibraryRole(undefined)}
            onConfirm={(ids) => {
              replaceRoleSelection(activeLibraryRole.key, ids);
              setLibraryRole(undefined);
            }}
            maxItems={
              isAmazon
                ? selections[activeLibraryRole.key].length + (amazonRemainingSourceSlots ?? 0)
                : featureId === "reference-remix" ? 1 : AMAZON_SOURCE_ASSET_LIMIT
            }
            disabled={Boolean(jobId) || !workspace?.id || createJob.isPending || uploadBusy}
          />
        ) : null}

        <footer className={styles.stickyBar}>
          <div>
            <span>{workspacePending ? <LoaderCircle className="spin" size={17} /> : <Clock3 size={17} />}</span>
            <p>
              <strong>{workspace?.name ?? "正在连接工作区"}</strong>
              <small>{jobId ? progressText : isAmazon ? "预计生成 21 张套图" : `预计生成 ${imageCount} 张图片`}</small>
              {!jobId && <CreditCostHint jobType={feature.jobType} count={expectedCount} />}
            </p>
          </div>
          <div className={styles.stickyActions}>
            {(uiError || requestError || createJob.error) ? (
              <p role="alert"><Info size={15} />{uiError ?? errorMessage(requestError ?? createJob.error)}</p>
            ) : !workflow.canSubmit ? (
              <p role="alert"><Info size={15} />{workflow.message}</p>
            ) : featureId === "reference-remix" && !jobId && splitList(sellingPoints).length === 0 ? (
              <p><Info size={15} />请填写真实商品事实与不可变特征。</p>
            ) : missingRequiredRole ? (
              <p><Info size={15} />请先选择：{missingRequiredRole.title}。</p>
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
                {createJob.isPending
                  ? "正在创建"
                  : isAmazon
                    ? "创建 21 项套图任务"
                    : `生成 ${imageCount} 张`}
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
