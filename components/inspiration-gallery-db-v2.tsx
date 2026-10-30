"use client";
import { QuantityControl } from "./creation-settings";
import { ReplicaSelect } from "./replica-select";
import { ProductFactsGuidance } from "./product-facts-guidance";

import { CreditCostHint } from "@/features/billing/components/credit-account";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { assetCreationHref } from "@/lib/assets/creation-handoff";
import { useQuery } from "@tanstack/react-query";
import { useBodyScrollLock } from "@/lib/hooks/use-body-scroll-lock";
import { JobProgressFeedback } from "@/components/job-progress-feedback";
import { ImageDropZone } from "@/components/image-drop-zone";
import { validateImageUpload } from "@/lib/image-upload";
import { jobNote } from "@/lib/jobs/presentation";
import { usePreviewVisibility } from "@/features/assets/hooks/use-preview-visibility";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, Copy, ImagePlus, Layers3, Loader2, Sparkles, X } from "lucide-react";
import { getAssetAccessUrl, useAssetAccessUrl, useAssets, useUploadAsset } from "@/features/assets/api/assets";
import { useCreateJob, useJob } from "@/features/jobs/api/jobs";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { useAuthSession } from "@/features/auth/api/session";
import { loginHref } from "@/lib/auth/return-to";
import { requestLogin } from "@/lib/auth/session-events";
import {
  presentWorkflow,
  resolveRuntimeCapability,
  useRuntimeStatus,
} from "@/features/runtime/api/runtime";
import type { Asset as ApiAsset } from "@/lib/api/types";
import styles from "./inspiration-gallery.module.css";

type Asset = { asset_id: string; role: "result" | "user_upload" | "reference"; ordinal: number; url: string };
type WorkflowType = "reference_remix" | "product_prompt" | "local_edit" | "legacy_unclassified";
type Case = { id: string; title: string; description: string; role: string; type: string; category: string; platform: string; ratio: string; intent?: string; prompt: string; workflow_type: WorkflowType; reference_source: "admin_reference" | "result_image" | "none"; configuration_status: string; configuration_errors: string[]; button_label: string; gallery: Asset[]; cover_image: Asset | null };
type RemixAspectRatio = "follow-reference" | "1:1" | "3:4" | "4:3" | "4:5" | "9:16" | "16:9";
type InspirationAspectRatio = "自动" | "16:9" | "4:3" | "4:5" | "3:2" | "1:1" | "2:3" | "3:4" | "5:4" | "9:16" | "21:9";

function isPromptTemplate(item: Case) {
  return !item.cover_image && item.workflow_type === "product_prompt";
}

function PromptTemplateArtwork({ item }: { item: Case }) {
  return <div className={styles.templateArtwork} data-variant={item.type}>
    <span>提示词模板</span>
    <Layers3 size={38} aria-hidden="true" />
    <strong>{item.title}</strong>
    <p>{item.description}</p>
    <small>上传商品图 · 应用原始指令</small>
  </div>;
}

const IMAGE_COUNT_OPTIONS = [1, 2, 3, 4] as const;
const ASPECT_OPTIONS: Array<{ value: InspirationAspectRatio; label: string }> = [
  { value: "16:9", label: "16:9" }, { value: "4:3", label: "4:3" }, { value: "4:5", label: "4:5" },
  { value: "3:2", label: "3:2" }, { value: "1:1", label: "1:1" }, { value: "2:3", label: "2:3" },
  { value: "3:4", label: "3:4" }, { value: "5:4", label: "5:4" }, { value: "9:16", label: "9:16" },
  { value: "21:9", label: "21:9" }, { value: "自动", label: "自动" },
];

// Case metadata may contain a legacy ratio such as 2:3 that is not accepted
// by the jobs API. In that situation the reference image is the authoritative
// canvas, so ask the backend to follow its actual dimensions instead of
// coercing it to a different crop or changing the case prompt.
function jobAspectRatio(ratio: string): RemixAspectRatio {
  return ["1:1", "3:4", "4:3", "4:5", "9:16", "16:9"].includes(ratio)
    ? ratio as RemixAspectRatio
    : "follow-reference";
}

function inspirationAspectToApi(value: InspirationAspectRatio): RemixAspectRatio {
  // "自动" must remain dynamic. The backend resolves it to the first
  // reference canvas, falling back to the first product canvas when no
  // explicit reference image is present. Do not silently coerce it to square.
  if (value === "自动") return "follow-reference";
  // Keep the Focus-style menu while adapting choices to the provider's
  // current canonical canvases.
  if (value === "1:1") return "1:1";
  if (value === "16:9" || value === "21:9") return "16:9";
  if (value === "4:3" || value === "3:2") return "4:3";
  if (value === "4:5" || value === "5:4") return "4:5";
  if (value === "2:3" || value === "9:16") return "9:16";
  return value;
}

function PersonalRepositoryThumbnail({
  asset,
  workspaceId,
  selected,
  onToggle,
}: {
  asset: ApiAsset;
  workspaceId: string;
  selected: boolean;
  onToggle: () => void;
}) {
  const { ref: previewRef, visible: previewVisible } = usePreviewVisibility<HTMLButtonElement>();
  const access = useAssetAccessUrl(workspaceId, previewVisible ? asset.id : undefined, "thumbnail");
  return (
    <button
      ref={previewRef}
      type="button"
      className={styles.repositoryTile}
      data-selected={selected || undefined}
      onClick={onToggle}
      aria-pressed={selected}
      aria-label={`${selected ? "取消选择" : "选择"} ${asset.original_filename}`}
    >
      {access.data?.url ? <img src={access.data.url} alt={asset.original_filename} loading="lazy" decoding="async" /> : <Loader2 className="spin" size={22} />}
      {selected && <span className={styles.repositoryCheck}>✓</span>}
    </button>
  );
}

export function InspirationGalleryDb() {
  const router = useRouter();
  const authSession = useAuthSession();
  const requestedCaseId = useSearchParams().get("case");
  const resumedCaseId = useRef<string | undefined>(undefined);
  const catalog = useQuery({
    queryKey: ["inspiration-cases"],
    retry: false,
    queryFn: async ({ signal }): Promise<Case[]> => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15_000);
      const abort = () => controller.abort();
      signal.addEventListener("abort", abort, { once: true });
      try {
        const response = await fetch("/api/v1/inspiration/cases", { signal: controller.signal });
        if (!response.ok) throw new Error("灵感服务暂时不可用，请重新连接后再试。");
        const value = await response.json();
        if (!Array.isArray(value.items)) throw new Error("灵感数据暂时无法读取，请重新连接后再试。");
        return value.items;
      } catch (reason) {
        if (controller.signal.aborted && !signal.aborted) {
          throw new Error("灵感服务响应超时，请重新连接后再试。");
        }
        throw reason;
      } finally {
        clearTimeout(timeout);
        signal.removeEventListener("abort", abort);
      }
    },
  });
  const items = useMemo(() => catalog.data ?? [], [catalog.data]);
  const loading = catalog.isPending;
  const error = catalog.isError ? catalog.error.message : null;
  const [type, setType] = useState("全部");
  const [category, setCategory] = useState("全部");
  const [platform, setPlatform] = useState("全部");
  const [selected, setSelected] = useState<Case | null>(null);
  const [activeCase, setActiveCase] = useState<Case | null>(null);
  const [composerAssets, setComposerAssets] = useState<Asset[]>([]);
  const [uploadingAssetId, setUploadingAssetId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string>();
  const [generating, setGenerating] = useState(false);
  const [composeError, setComposeError] = useState<string>();
  const [instruction, setInstruction] = useState("");
  const [copied, setCopied] = useState(false);
  const [previewAsset, setPreviewAsset] = useState<Asset | null>(null);
  const [repositoryOpen, setRepositoryOpen] = useState(false);
  useBodyScrollLock(Boolean(selected || repositoryOpen || previewAsset));
  const [repositoryPage, setRepositoryPage] = useState(1);
  const [repositorySelected, setRepositorySelected] = useState<string[]>([]);
  const [imageCount, setImageCount] = useState(1);
  const [aspectRatio, setAspectRatio] = useState<InspirationAspectRatio>("自动");
  const [materializingCaseId, setMaterializingCaseId] = useState<string | null>(null);
  const [caseImportFailed, setCaseImportFailed] = useState(false);
  const composerRef = useRef<HTMLDivElement>(null);
  const composerMutationLock = useRef(false);
  const { workspace, workspaceState } = useWorkspace();
  const upload = useUploadAsset(workspace?.id);
  const createJob = useCreateJob(workspace?.id);
  const runtime = useRuntimeStatus();
  const creativeWorkflow = presentWorkflow(
    resolveRuntimeCapability(runtime.data, "creative_image"),
  );
  const referenceWorkflow = presentWorkflow(
    resolveRuntimeCapability(runtime.data, "reference_remix"),
  );
  const launchWorkflow =
    activeCase?.workflow_type === "reference_remix"
      ? referenceWorkflow
      : creativeWorkflow;
  const repositoryQuery = useAssets(workspace?.id, { originKind: "upload", limit: 100 });
  const repositoryAssets = repositoryQuery.data?.items ?? [];
  const repositoryPageSize = 10;
  const repositoryPageCount = Math.max(1, Math.ceil(repositoryAssets.length / repositoryPageSize));
  const repositoryPageAssets = repositoryAssets.slice(
    (repositoryPage - 1) * repositoryPageSize,
    repositoryPage * repositoryPageSize,
  );
  const job = useJob(workspace?.id, jobId);
  const composerBusy = uploadingAssetId !== null || Boolean(materializingCaseId) || generating || createJob.isPending;
  const composerDisabled = composerBusy || !workspace?.id;
  const remainingSourceSlots = Math.max(0, 8 - composerAssets.filter((asset) => asset.role !== "result").length);

  useEffect(() => {
    const detail = job.data;
    if (!detail || !jobId) return;

    if (["failed", "cancelled"].includes(detail.status)) {
      // The job polling hook is an external subscription; synchronize the
      // local composer state when its status changes.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setGenerating(false);
      setComposeError(detail.status === "cancelled" ? "任务已取消，素材和要求已保留。" : jobNote(detail));
      return;
    }

    const partial = detail.status === "partial";
    const outputAssets = detail.assets
      .filter((asset) => asset.direction === "output" && asset.is_deliverable)
      .sort((left, right) => left.ordinal - right.ordinal);
    if ((!partial && detail.status !== "completed") || !outputAssets.length || !workspace?.id) {
      if (partial) {
        setGenerating(false);
        setComposeError("部分图片已生成，失败项可在生成记录中重试。");
      }
      return;
    }

    let cancelled = false;
    void Promise.all(outputAssets.map(async (asset) => ({
      asset_id: asset.asset_id,
      role: "result" as const,
      ordinal: asset.ordinal,
      url: (await getAssetAccessUrl(workspace.id, asset.asset_id)).url,
    })))
      .then((results) => {
        if (cancelled) return;
        setComposerAssets((current) => [
          ...current.filter((asset) => asset.role !== "result"),
          ...results,
        ]);
        setGenerating(false);
        setComposeError(
          partial
            ? "部分图片已生成，失败项可在生成记录中重试。"
            : undefined,
        );
      })
      .catch(() => {
        if (cancelled) return;
        setGenerating(false);
        setComposeError("生成已完成，但结果图片暂时无法加载，请稍后重试。");
      });

    return () => {
      cancelled = true;
    };
  // The selected fields above are the stable polling signals; depending on the
  // whole response object would rerun this effect for unrelated query updates.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.data?.status, job.data?.error_message, job.data?.primary_output_asset_id, jobId, workspace?.id]);

  useEffect(() => {
    // Older builds persisted a second auto-submit handoff for the remix
    // workspace. Inspiration now submits directly with workspace-local copies,
    // so discard any stale handoff left by a previous failed attempt.
    try { window.localStorage.removeItem("polaris-inspiration-strategy"); }
    catch { /* Browser storage restrictions must not block the public catalog. */ }
  }, []);

  const types = useMemo(() => ["全部", ...new Set(items.map((item) => item.type))], [items]);
  const categories = useMemo(() => ["全部", ...new Set(items.map((item) => item.category))], [items]);
  const filtered = items.filter((item) => (type === "全部" || item.type === type) && (category === "全部" || item.category === category) && (platform === "全部" || item.platform === platform));
  const templateCount = items.filter(isPromptTemplate).length;
  const hasFilters = type !== "全部" || category !== "全部" || platform !== "全部";

  const selectCase = async (item: Case) => {
    if (!authSession.data) {
      requestLogin(`/inspiration?case=${encodeURIComponent(item.id)}`);
      return;
    }
    if (!workspace?.id) {
      setComposeError(workspaceState === "loading" ? "工作区正在准备，请稍后再试。" : "工作区不可用，请稍后再试。");
      return;
    }
    if (materializingCaseId || composerMutationLock.current || generating || createJob.isPending) return;
    setSelected(null);
    setActiveCase(item);
    setCaseImportFailed(false);
    setJobId(undefined);
    setComposerAssets([]);
    // Keep the confirmed case prompt visible in the composer. Any text the
    // user edits or appends remains a local override when the job is sent.
    setInstruction(item.prompt);
    const caseRatio = jobAspectRatio(item.ratio);
    setAspectRatio(caseRatio === "follow-reference" ? "自动" : caseRatio);
    setComposeError(undefined);
    setMaterializingCaseId(item.id);
    composerRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    try {
      // The API is the source of truth for case asset roles. Results are
      // display-only and must never be promoted to a product or reference
      // input by the client.
      const sourceAssets = item.gallery.filter((asset) => asset.role !== "result");
      const copied = await Promise.all(sourceAssets.map(async (asset) => {
        const response = await fetch(asset.url);
        if (!response.ok) throw new Error("案例素材暂时无法读取。");
        const blob = await response.blob();
        const contentType = blob.type || "image/png";
        const extension = contentType === "image/jpeg" ? "jpg" : contentType === "image/webp" ? "webp" : "png";
        const file = new File([blob], `inspiration-${item.id}-${asset.role}-${asset.ordinal}.${extension}`, { type: contentType });
        const uploaded = await upload.mutateAsync({ file, mediaKind: "image", originKind: "imported" });
        const access = await getAssetAccessUrl(workspace.id, uploaded.id);
        return { ...asset, asset_id: uploaded.id, url: access.url } satisfies Asset;
      }));
      if (item.workflow_type === "local_edit") {
        const sourceIds = copied.filter(asset => asset.role === "user_upload").map(asset => asset.asset_id);
        router.push(assetCreationHref("/workspace/local-edit", workspace.id, sourceIds, item.id));
        return;
      }
      setActiveCase(item);
      setComposerAssets(copied);
    } catch {
      // Keep the selected workflow on failure. Falling back to freeform here
      // silently submits the case prompt without its required references.
      setCaseImportFailed(true);
      setComposerAssets([]);
      setComposeError("案例素材导入失败，请重新导入后再生成；商品图与参考图尚未准备完成。");
    } finally {
      setMaterializingCaseId(null);
    }
  };

  const replaceComposerAsset = async (asset: Asset, file: File) => {
    if (!workspace?.id || composerDisabled || composerMutationLock.current) return;
    const selected = validateImageUpload([file]);
    if (selected.error) { setComposeError(selected.error); return; }
    composerMutationLock.current = true;
    setUploadingAssetId(asset.asset_id);
    try {
      const uploaded = await upload.mutateAsync({ file, mediaKind: "image" });
      const access = await getAssetAccessUrl(workspace.id, uploaded.id);
      setComposerAssets((current) => current.map((entry) => entry.asset_id === asset.asset_id
        ? { ...entry, asset_id: uploaded.id, url: access.url }
        : entry));
      setComposeError(undefined);
    } catch (reason) {
      setComposeError(reason instanceof Error ? reason.message : "图片上传失败，请稍后重试。");
    } finally {
      composerMutationLock.current = false;
      setUploadingAssetId(null);
    }
  };

  const addComposerAssets = async (files: File[], role: "reference" | "user_upload") => {
    if (!workspace?.id || composerDisabled || composerMutationLock.current) return;
    const selected = validateImageUpload(files, { multiple: true, maxFiles: remainingSourceSlots });
    if (selected.error) { setComposeError(selected.error); return; }
    if (!selected.files.length) return;
    composerMutationLock.current = true;
    setUploadingAssetId("new");
    try {
      // Wait for the entire batch, including failed requests, before unlocking.
      const outcomes = await Promise.allSettled(selected.files.map(async (file) => {
        const uploaded = await upload.mutateAsync({ file, mediaKind: "image" });
        const access = await getAssetAccessUrl(workspace.id!, uploaded.id);
        return { asset_id: uploaded.id, role, ordinal: 0, url: access.url } satisfies Asset;
      }));
      const nextAssets = outcomes.flatMap((outcome) => outcome.status === "fulfilled" ? [outcome.value] : []);
      setComposerAssets((current) => {
        const roleOffset = current.filter((entry) => entry.role === role).length;
        return [...current, ...nextAssets.map((asset, index) => ({ ...asset, ordinal: roleOffset + index }))];
      });
      const failed = outcomes.filter((outcome) => outcome.status === "rejected").length;
      setComposeError(failed ? `已添加 ${nextAssets.length} 张，${failed} 张上传失败，请重新上传失败的图片。` : undefined);
    } finally {
      composerMutationLock.current = false;
      setUploadingAssetId(null);
    }
  };

  const removeComposerAsset = (asset: Asset) => {
    if (composerDisabled || composerMutationLock.current) return;
    setComposerAssets((current) => current.filter((entry) => entry.asset_id !== asset.asset_id));
    setComposeError(undefined);
  };

  const openRepository = () => {
    if (composerDisabled || composerMutationLock.current) return;
    setRepositorySelected(composerAssets.filter((asset) => asset.role === "user_upload").map((asset) => asset.asset_id));
    setRepositoryPage(1);
    setRepositoryOpen(true);
  };

  const confirmRepository = async () => {
    if (!workspace?.id || composerDisabled || composerMutationLock.current) return;
    const selectedAssets = repositoryAssets.filter((asset) => repositorySelected.includes(asset.id));
    const referenceCount = composerAssets.filter((asset) => asset.role === "reference").length;
    if (selectedAssets.length + referenceCount > 8) {
      setComposeError(`商品图与参考图合计最多 8 张，当前参考图 ${referenceCount} 张。`);
      return;
    }
    composerMutationLock.current = true;
    setUploadingAssetId("repository");
    try {
      const resolved = await Promise.all(selectedAssets.map(async (asset, ordinal) => ({
        asset_id: asset.id,
        role: "user_upload" as const,
        ordinal,
        url: (await getAssetAccessUrl(workspace.id, asset.id)).url,
      })));
      setComposerAssets((current) => [
        ...current.filter((asset) => asset.role !== "user_upload" && asset.role !== "result"),
        ...resolved,
      ]);
      setComposeError(undefined);
      setRepositoryOpen(false);
    } catch {
      setComposeError("个人仓库素材暂时无法读取，请稍后重试。");
    } finally {
      composerMutationLock.current = false;
      setUploadingAssetId(null);
    }
  };

  const casePromptWithOverride = (basePrompt: string, override: string) => {
    const normalizedOverride = override.trim();
    if (!normalizedOverride) return basePrompt;
    return `${basePrompt}\n\nUSER OVERRIDE — HIGHEST PRIORITY\nApply the following user-requested local changes exactly. These requirements override any conflicting wording in the case prompt and must not be paraphrased, weakened, or replaced. Keep every other case composition, product role, prop position, crop, scale, typography zone, and layout constraint unchanged:\n${normalizedOverride}`;
  };

  const launchRemix = async () => {
    if (composerDisabled || composerMutationLock.current) return;
    if (caseImportFailed) {
      setComposeError("请先重新导入案例素材，避免缺少参考图。");
      return;
    }
    const remixCase = activeCase;
    const requiredWorkflow =
      !remixCase || remixCase.workflow_type === "product_prompt"
        ? creativeWorkflow
        : remixCase.workflow_type === "reference_remix"
          ? referenceWorkflow
          : undefined;
    if (requiredWorkflow && !requiredWorkflow.canSubmit) {
      setComposeError(requiredWorkflow.message);
      return;
    }
    if (!remixCase) {
      if (!instruction.trim()) return;
      if (!workspace?.id) {
        setComposeError(workspaceState === "loading" ? "工作区正在准备，请稍后再试。" : "工作区不可用，请稍后再试。");
        return;
      }
      const productAssetIds = composerAssets.filter((asset) => asset.role === "user_upload").map((asset) => asset.asset_id);
      if (productAssetIds.length === 0) {
        setComposeError("请先上传一张商品图，再开始生成。");
        return;
      }
      setComposeError(undefined);
      setJobId(undefined);
      setGenerating(true);
      createJob.mutate({
        intent: "submit",
        type: "creative_image",
        display_name: "灵感创作",
        input: {
          product_asset_ids: productAssetIds,
          competitor_asset_ids: composerAssets.filter((asset) => asset.role === "reference").map((asset) => asset.asset_id),
        },
        configuration: {
          creative_direction: "selling_point",
          platform_code: "generic",
          language: "zh-CN",
          selling_points: [],
          aspect_ratio: inspirationAspectToApi(aspectRatio),
          image_count: imageCount,
          // Opt in only for this Inspiration tab's freeform mode. Other
          // creative-image surfaces keep their existing direct contract.
          inspiration_planning: true,
          inspiration_reference_planning: composerAssets.some((asset) => asset.role === "reference"),
          instruction: instruction.trim(),
        },
      }, {
        onSuccess: (created) => setJobId(created.id),
        onError: (reason) => {
          setGenerating(false);
          setComposeError(reason instanceof Error ? reason.message : "生成请求提交失败，请稍后重试。");
        },
      });
      return;
    }
    if (remixCase.workflow_type === "legacy_unclassified") {
      setComposeError("该案例正在配置中，暂不支持一键使用。");
      return;
    }
    if (remixCase.workflow_type === "local_edit") {
      setComposeError("该案例需要原图与编辑区域 Mask，当前页面暂未开放局部编辑入口。");
      return;
    }
    // Keep the canonical case prompt byte-for-byte unless the user edits or
    // appends text in the composer. Do not rewrite it according to workflow.
    const compiledPrompt = remixCase.prompt;
    const normalizedInstruction = instruction.trim();
    // The composer is prefilled with the complete confirmed case prompt. When
    // the user appends a description, only the suffix is the override; sending
    // the whole textarea value as an override would duplicate the case prompt
    // and can exceed the API's 4,000-character validation limit.
    const hasCanonicalPrefix = normalizedInstruction.startsWith(compiledPrompt);
    const userOverride = hasCanonicalPrefix
      ? normalizedInstruction.slice(compiledPrompt.length).trim()
      : "";
    // If the user edited the prefilled prompt itself rather than appending to
    // it, respect that explicit full-text edit instead of duplicating it.
    const finalPrompt = hasCanonicalPrefix
      ? casePromptWithOverride(compiledPrompt, userOverride)
      : normalizedInstruction;
    const productAssetIds = composerAssets.filter((asset) => asset.role === "user_upload").map((asset) => asset.asset_id);
    const referenceAssetIds = composerAssets.filter((asset) => asset.role === "reference").map((asset) => asset.asset_id);
    if (remixCase.workflow_type === "product_prompt") {
      if (!workspace?.id) {
        setComposeError(workspaceState === "loading" ? "工作区正在准备，请稍后再试。" : "工作区不可用，请稍后再试。");
        return;
      }
      if (productAssetIds.length === 0) {
        setComposeError("该案例只需要商品图；请先上传一张商品图。");
        return;
      }
      setComposeError(undefined);
      setJobId(undefined);
      setGenerating(true);
      createJob.mutate({
        intent: "submit",
        inspiration_case_id: remixCase.id,
        type: "creative_image",
        display_name: `${remixCase.title} · 商品提示词`,
        input: { product_asset_ids: productAssetIds, competitor_asset_ids: [] },
        configuration: {
          creative_direction: "scenario",
          platform_code: "generic",
          language: "zh-CN",
          selling_points: [],
          aspect_ratio: inspirationAspectToApi(aspectRatio),
          image_count: imageCount,
          inspiration_planning: true,
          inspiration_reference_planning: false,
          instruction: finalPrompt,
        },
      }, {
        onSuccess: (created) => setJobId(created.id),
        onError: (reason) => { setGenerating(false); setComposeError(reason instanceof Error ? reason.message : "生成请求提交失败，请稍后重试。"); },
      });
      return;
    }
    if (!workspace?.id) {
      setComposeError(workspaceState === "loading" ? "工作区正在准备，请稍后再试。" : "工作区不可用，请稍后再试。");
      return;
    }
    if (productAssetIds.length === 0 || referenceAssetIds.length === 0) {
      setComposeError("案例素材尚未准备好，请重新选择案例后再试。");
      return;
    }
    try {
      await Promise.all([...productAssetIds, ...referenceAssetIds].map((assetId) => getAssetAccessUrl(workspace.id, assetId)));
    } catch {
      setComposeError("替换后的图片已失效，请在当前页面重新上传后再生成。");
      return;
    }
    // Clear a previous failed attempt before starting a new remix. Otherwise a
    // later successful result leaves the stale generic failure message visible
    // because composeError is rendered whenever generating becomes false.
    setComposeError(undefined);
    // Do not let the previous job detail (especially a failed job) terminate
    // this attempt before the create request returns a new job id.
    setJobId(undefined);
    setGenerating(true);
    createJob.mutate({
      intent: "submit",
      inspiration_case_id: remixCase.id,
      type: "reference_remix",
      display_name: "灵感同款创作",
      input: {
        product_asset_ids: productAssetIds,
        reference_asset_ids: referenceAssetIds,
      },
      configuration: {
        reference_skill: "reference-visual-reverse-remix",
        platform_code: "tmall",
        language: "zh-CN",
        selling_points: [],
        aspect_ratio: inspirationAspectToApi(aspectRatio),
        image_count: imageCount,
        // Let the verified reference-visual-reverse-remix Skill inspect the
        // current product/reference pair and compile the executable prompt.
        // The published case prompt remains unchanged and is passed as guidance;
        // it must not be used as a literal prompt for a different product.
        execution_mode: "planner" as const,
        // Keep the multipart order aligned with the case prompt: Image 1 is the
        // user's product and Image 2 is the visual-strategy reference.
        inspiration_product_first: true,
        quality: "high" as const,
        output_format: "png" as const,
        instruction: finalPrompt,
        product_identity_lock: "Product image is the only identity authority. Preserve product geometry, materials, packaging, visible brand and text exactly; do not invent or rewrite product text.",
        reference_visual_analysis: "Analyze the reference image for canvas, crop, layout, subject positions, safe areas, camera, lighting, typography zones, and commercial intent. Transfer relationships and visual grammar only.",
        commercial_intent: "Create an original ecommerce advertisement that makes the user product the hero and preserves believable product-person contact where present.",
        reusable_visual_strategy: "Reuse composition, scale hierarchy, spatial relations, camera, lighting, and commercial roles as abstract structure, never source nouns or proprietary assets.",
        original_remix_prompt: finalPrompt,
        forbidden_transfer: "Do not transfer source brand, logo, watermark, exact copy, numbers, certification, proprietary assets, unsupported claims, or hidden prompts.",
        post_generation_review: "Before accepting, verify product identity and visible product text, reference visual strategy, role relationships, and absence of source-brand leakage.",
      },
    }, {
      onSuccess: (created) => setJobId(created.id),
      onError: (reason) => {
        setGenerating(false);
        setComposeError(reason instanceof Error ? reason.message : "生成请求提交失败，请稍后重试。");
      },
    });
  };

  const copy = async () => {
    if (!selected) return;
    await navigator.clipboard?.writeText(selected.prompt);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  // The prompt shown in the drawer is the canonical prompt that is handed to
  // the reference-remix task. Do not strip Skill section markers here: doing
  // so made the UI copy differ from the persisted task input and erased the
  // boundaries that the Skill uses to separate identity, strategy, copy, and
  // forbidden-transfer rules.
  const displayPrompt = (prompt: string) => prompt;
  useEffect(() => {
    if (!authSession.data || !workspace?.id || !requestedCaseId || resumedCaseId.current === requestedCaseId) return;
    const item = items.find((entry) => entry.id === requestedCaseId);
    if (!item) return;
    resumedCaseId.current = requestedCaseId;
    const timer = window.setTimeout(() => { void selectCase(item); }, 0);
    return () => window.clearTimeout(timer);
  }, [authSession.data, workspace?.id, requestedCaseId, items, selectCase]);

  const resultAssets = composerAssets.filter((asset) => asset.role === "result");
  const resultAsset = resultAssets[0];

  return <div className={styles.page}>
    <section className={styles.hero}>
      <div className={styles.heroInner}>
        <div className={styles.heroTitle}><Sparkles size={27} /><h1>找一个好灵感，做成自己的好商品。</h1></div>
        <p className={styles.discoverySteps}>先看案例 <span>→</span> 查看创作指令 <span>→</span> 上传商品做同款 <a href="#inspiration-cases">浏览灵感素材 ↓</a></p>
        {authSession.data !== true && <p><Link href={loginHref("/inspiration")} className={styles.modalApply} onClick={(event) => { event.preventDefault(); requestLogin("/inspiration"); }}>登录后开始创作</Link></p>}
        <div className={`${styles.command} ${activeCase || composerAssets.length > 0 || generating ? styles.commandWithAssets : ""}`} ref={composerRef}>
          <ImageDropZone label="灵感商品图" className={styles.composerDropZone} multiple maxFiles={remainingSourceSlots}
            disabled={composerDisabled} busy={uploadingAssetId !== null} onFiles={(files) => addComposerAssets(files, "user_upload")}>
            {({ openFileDialog, disabled: addDisabled }) => <div className={`${styles.uploadBox} ${generating ? styles.composerGenerating : ""}`} aria-label="案例素材">
              {composerAssets.filter((asset) => asset.role !== "result").map((asset) => (
                <ImageDropZone key={asset.asset_id} label={`替换${asset.role === "reference" ? "参考图" : "商品图"} ${asset.ordinal + 1}`}
                  className={styles.composerAssetTile} showHint={false} disabled={composerDisabled}
                  busy={uploadingAssetId === asset.asset_id} onFiles={async ([file]) => { if (file) await replaceComposerAsset(asset, file); }}>
                  {({ openFileDialog: replaceFile, disabled: replaceDisabled }) => <>
                    <button type="button" className={styles.assetPreviewButton} aria-label="放大查看上传图片" onClick={() => setPreviewAsset(asset)}><img src={asset.url} alt={asset.role === "reference" ? "参考图" : "用户上传图"} /></button>
                    <span>{asset.role === "reference" ? "参考图" : "用户上传"}{uploadingAssetId === asset.asset_id ? " · 上传中" : ""}</span>
                    <button type="button" className={styles.assetReplaceButton} disabled={replaceDisabled} onClick={replaceFile}>替换</button>
                    <button type="button" className={styles.assetRemoveButton} aria-label="删除上传图片" disabled={composerDisabled} onClick={() => removeComposerAsset(asset)}><X size={13} /></button>
                  </>}
                </ImageDropZone>
              ))}
              {materializingCaseId && <Loader2 className="spin" size={24} />}
              <button type="button" className={styles.composerAddTile} aria-label="添加商品图" disabled={addDisabled} onClick={openFileDialog}>
                <ImagePlus size={22} /><span>添加商品图</span>
              </button>
            </div>}
          </ImageDropZone>
          <textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="请详细填写真实商品卖点、资料与希望呈现的画面，可粘贴商品介绍。" /><ProductFactsGuidance />
          <div className={styles.composerControls}>
            <button type="button" className={styles.repositoryButton} onClick={openRepository} disabled={composerDisabled}><Layers3 size={15} /> 素材库</button>
            <div className={styles.composerSetting}><span>比例</span><ReplicaSelect label="选择生成图片比例" size="compact" value={aspectRatio} onChange={value => setAspectRatio(value as InspirationAspectRatio)} disabled={Boolean(materializingCaseId)} options={ASPECT_OPTIONS.map(option => [option.value,option.label] as const)} /></div>
            <div className={styles.composerSetting}><span>数量</span><QuantityControl label="生成张数" compact value={imageCount} onChange={setImageCount} allowedValues={IMAGE_COUNT_OPTIONS} disabled={composerDisabled} /></div>
            <CreditCostHint jobType={activeCase?.workflow_type === "reference_remix" ? "reference_remix" : "creative_image"} count={imageCount} variant="inspiration" fallback={<small>灵感创作 · 5 积分/张</small>} />
          <button className={styles.commandSubmit} aria-label={activeCase ? activeCase.button_label : "开始创作"} onClick={launchRemix} disabled={composerDisabled || caseImportFailed || !launchWorkflow.canSubmit || (activeCase ? activeCase.workflow_type === "legacy_unclassified" || activeCase.workflow_type === "local_edit" || composerAssets.every((asset) => asset.role !== "user_upload" || !asset.asset_id) || (activeCase.workflow_type === "reference_remix" && composerAssets.every((asset) => asset.role !== "reference" || !asset.asset_id)) : !instruction.trim())} style={{ opacity: materializingCaseId || generating || activeCase || instruction.trim() ? 1 : .42 }}>{materializingCaseId ? <Loader2 className="spin" size={17} /> : generating ? <Loader2 className="spin" size={17} /> : <ChevronRight size={18} />}</button>
          </div>
          {activeCase && activeCase.workflow_type === "legacy_unclassified" && <p style={{ margin: "8px 0 0", color: "#b45309", fontSize: 12 }}>该案例正在配置中，暂不支持一键使用。</p>}
          {activeCase && activeCase.workflow_type === "reference_remix" && !composerAssets.some((asset) => asset.role === "reference") && <p style={{ margin: "8px 0 0", color: "#b45309", fontSize: 12 }}>该案例需要商品图 + 参考图；可替换或补充参考图后生成。</p>}
          {activeCase && activeCase.workflow_type === "product_prompt" && !composerAssets.some((asset) => asset.role === "user_upload") && <p style={{ margin: "8px 0 0", color: "#b45309", fontSize: 12 }}>{isPromptTemplate(activeCase) ? "该模板保留原始指令，上传自己的商品图即可使用。" : "该案例只需要商品图，不需要参考图。"}</p>}
        </div>
        {resultAsset && <div className={styles.resultShowcase}><div className={styles.resultShowcaseMeta}><strong>本次生成结果 · {resultAssets.length} 张</strong><span>点击图片查看大图</span></div><div className={styles.resultShowcaseGrid}>{resultAssets.map((asset) => <button type="button" key={asset.asset_id} aria-label={`查看第 ${asset.ordinal + 1} 张生成结果`} onClick={() => setPreviewAsset(asset)}><img src={asset.url} alt={`生成结果 ${asset.ordinal + 1}`} /></button>)}</div></div>}
        {materializingCaseId ? <div className={styles.generationStatus} role="status"><Loader2 className="spin" size={15} /><span>正在准备案例素材</span></div> : (generating || jobId) ? <div className={styles.progressFeedback}>
          <JobProgressFeedback job={job.data} pending={createJob.isPending || (generating && !job.data)} error={job.error}
            lastSyncedAt={job.dataUpdatedAt} refreshing={job.isFetching} onRefresh={jobId ? () => void job.refetch() : undefined}
            summary={generating && job.data?.status === "completed" ? "生成已完成，正在载入结果图片" : undefined} />
        </div> : null}
        {composeError && !generating ? <div className={styles.composeError} role="alert">{composeError}</div> : null}
        {caseImportFailed && activeCase && <button type="button" className={styles.caseImportRetry} disabled={composerDisabled} onClick={() => void selectCase(activeCase)}>重新导入案例素材</button>}
      </div>
    </section>
    <main className={styles.content}><section className={styles.materialPanel} id="inspiration-cases"><div className={styles.sectionHead}><div><p className={styles.eyebrow}>CURATED VISUAL LIBRARY</p><h2>灵感素材</h2></div><span className={styles.count}>{filtered.length} 个方案</span></div><div className={styles.catalogSummary} role="status"><span>{loading ? "正在读取灵感素材…" : error ? "灵感素材尚未加载" : <>共 <strong>{items.length}</strong> 个灵感 · {items.length - templateCount} 组案例 · {templateCount} 个提示词模板</>}</span><div><span>当前显示 {filtered.length} 个</span>{hasFilters && <button type="button" onClick={() => { setType("全部"); setCategory("全部"); setPlatform("全部"); }}>清除筛选</button>}</div></div><div className={styles.filters}><div className={styles.filterRow}><b>类型:</b><div>{types.map((value) => <button key={value} className={type === value ? styles.active : ""} onClick={() => setType(value)}>{value}</button>)}</div></div><div className={styles.filterRow}><b>品类:</b><div>{categories.map((value) => <button key={value} className={category === value ? styles.active : ""} onClick={() => setCategory(value)}>{value}</button>)}</div></div><div className={styles.filterRow}><b>分类:</b><div>{["全部", "国内", "跨境"].map((value) => <button key={value} className={platform === value ? styles.active : ""} onClick={() => setPlatform(value)}>{value}</button>)}</div></div></div>{loading ? <div className={styles.emptyInput}><Loader2 /> 正在加载灵感案例…</div> : error ? <div className={styles.catalogEmpty} role="alert"><strong>灵感素材暂时无法加载</strong><span>{error}</span><button type="button" disabled={catalog.isFetching} onClick={() => void catalog.refetch()}>{catalog.isFetching ? "正在重新连接…" : "重新连接"}</button></div> : filtered.length === 0 ? <div className={styles.catalogEmpty}><strong>{items.length ? "没有符合当前筛选的案例" : "灵感素材正在准备中"}</strong>{items.length ? <button type="button" onClick={() => { setType("全部"); setCategory("全部"); setPlatform("全部"); }}>清除筛选，查看全部案例</button> : <Link href="/#start">先去开始创作</Link>}</div> : <div className={styles.grid}>{filtered.map((item) => <article className={styles.card} key={item.id}><div className={styles.imageWrap}>{item.cover_image?.url ? <img src={`${item.cover_image.url}?variant=thumbnail`} alt={item.title} loading="lazy" decoding="async" /> : isPromptTemplate(item) ? <PromptTemplateArtwork item={item} /> : null}<span>{item.platform}</span><div className={styles.imageHover}><button onClick={() => setSelected(item)}>查看指令</button></div></div><div className={styles.cardBody}><div className={styles.cardTags}><span>{item.type}</span><span>{item.category}</span></div><h3>{item.title}</h3><p>{item.description}</p><div className={styles.cardFooter}><small>{item.role} · {item.ratio}</small><button onClick={() => selectCase(item)}>{item.button_label || "一键做同款"} <ChevronRight size={15} /></button></div></div></article>)}</div>}</section></main>
    {repositoryOpen && <div className={styles.repositoryOverlay} role="dialog" aria-modal="true" aria-labelledby="personal-repository-title" onMouseDown={(event) => { if (event.target === event.currentTarget) setRepositoryOpen(false); }}><section className={styles.repositoryModal} onMouseDown={(event) => event.stopPropagation()}><header className={styles.repositoryHeader}><h2 id="personal-repository-title">个人仓库</h2><button type="button" className={styles.repositoryClose} aria-label="关闭个人仓库" onClick={() => setRepositoryOpen(false)}><X size={21} /></button></header><div className={styles.repositoryBody}>{repositoryQuery.isPending ? <div className={styles.repositoryState}><Loader2 className="spin" size={22} /> 正在加载个人素材…</div> : repositoryQuery.isError ? <div className={styles.repositoryState}>个人素材暂时无法读取，请稍后重试。</div> : repositoryPageAssets.length ? <div className={styles.repositoryGrid}>{repositoryPageAssets.map((asset) => <PersonalRepositoryThumbnail key={asset.id} asset={asset} workspaceId={workspace!.id} selected={repositorySelected.includes(asset.id)} onToggle={() => setRepositorySelected((current) => current.includes(asset.id) ? current.filter((id) => id !== asset.id) : [...current, asset.id])} />)}</div> : <div className={styles.repositoryState}>还没有用户上传素材，请先上传图片。</div>}</div><footer className={styles.repositoryFooter}><div className={styles.repositoryPager}><button type="button" aria-label="上一页" disabled={repositoryPage <= 1} onClick={() => setRepositoryPage((page) => Math.max(1, page - 1))}>‹</button>{Array.from({ length: repositoryPageCount }, (_, index) => index + 1).slice(0, 5).map((page) => <button type="button" key={page} className={repositoryPage === page ? styles.repositoryPageSelected : ""} onClick={() => setRepositoryPage(page)}>{page}</button>)}<button type="button" aria-label="下一页" disabled={repositoryPage >= repositoryPageCount} onClick={() => setRepositoryPage((page) => Math.min(repositoryPageCount, page + 1))}>›</button><span>{repositoryAssets.length ? `${repositoryAssets.length} 个上传素材` : "0 个上传素材"}</span></div><div className={styles.repositoryActions}><button type="button" onClick={() => setRepositoryOpen(false)}>取消</button><button type="button" className={styles.repositoryConfirm} onClick={() => void confirmRepository()} disabled={composerDisabled || repositoryQuery.isPending}>确定</button></div></footer></section></div>}
    {previewAsset && <div className={styles.resultPreviewOverlay} role="dialog" aria-label="生成成品大图" onMouseDown={(event) => { if (event.target === event.currentTarget) setPreviewAsset(null); }}><button type="button" className={styles.resultPreviewClose} aria-label="关闭大图预览" onClick={() => setPreviewAsset(null)}><X size={22} /></button><img src={previewAsset.url} alt="生成成品大图" /></div>}
    {selected && <div className={styles.overlay} onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null); }}><aside className={styles.focusModal}><button className={styles.close} onClick={() => setSelected(null)}><X size={20} /></button><div className={`${styles.modalGallery} ${isPromptTemplate(selected) ? styles.templateModal : ""}`}>{isPromptTemplate(selected) ? <PromptTemplateArtwork item={selected} /> : <><div className={styles.inputGrid}>{selected.gallery.filter((asset) => asset.role !== "result").map((asset) => <div className={styles.inputTile} key={asset.asset_id}><span>{asset.role === "user_upload" ? "用户上传" : "参考图"}</span><img src={asset.url} alt="案例输入" /></div>)}</div><div className={styles.resultTile}>{selected.gallery.filter((asset) => asset.role === "result").map((asset) => <img key={asset.asset_id} src={asset.url} alt="案例结果" />)}</div></>}</div><div className={styles.modalInfo}><h2>{selected.title}</h2><h3>创作指令</h3><p>{displayPrompt(selected.prompt)}</p><button className={styles.modalApply} onClick={copy}>{copied ? "已复制" : <><Copy size={15} /> 复制指令</>}</button><button className={styles.modalApply} onClick={() => selectCase(selected)}>{selected.button_label || "一键做同款"}</button></div></aside></div>}
  </div>;
}
