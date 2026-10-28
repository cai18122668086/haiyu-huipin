"use client";

import { CreditCostHint } from "@/features/billing/components/credit-account";

import { useMutation } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  Clock3,
  Image as ImageIcon,
  Images,
  Info,
  LoaderCircle,
  RefreshCcw,
  ShieldCheck,
  Sparkles,
  Upload,
  UserRound,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { ImageDropZone } from "@/components/image-drop-zone";
import { JobProgressFeedback } from "@/components/job-progress-feedback";
import { AppShell } from "@/components/app-shell";
import { WorkspaceJobGate } from "@/components/workspace-job-gate";
import { useWorkspaceJobId, useWorkspaceJobReview } from "@/lib/jobs/use-workspace-job-id";
import { workspaceJobSnapshot } from "@/lib/jobs/workspace-restore";
import { AssetLibraryDialog } from "@/components/asset-library-picker";
import { StatusBadge } from "@/components/status-badge";
import { ResultGrid } from "@/components/result-grid";
import { SkillReviewChecklist } from "@/components/skill-review-checklist";
import { WorkspaceHeader } from "@/components/workspace-header";
import { useAsset, useAssetAccessUrl, useUploadAsset } from "@/features/assets/api/assets";
import { useCreateJob, useJob } from "@/features/jobs/api/jobs";
import {
  presentWorkflow,
  resolveRuntimeCapability,
  useRuntimeStatus,
} from "@/features/runtime/api/runtime";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { apiClient } from "@/lib/api/client";
import { IdempotencyIntentRegistry } from "@/lib/api/idempotency";
import { errorMessage, unwrapApiResponse } from "@/lib/api/request";
import type { Asset, JobCreate } from "@/lib/api/types";
import { getFeatureById } from "@/lib/feature-registry";
import { IMAGE_UPLOAD_FORMAT_HINT } from "@/lib/image-upload";
import { jobNote } from "@/lib/jobs/presentation";
import { selectJobResultAssets } from "@/lib/jobs/results";
import styles from "./model-pose-transfer-workspace.module.css";

type PoseGender = "female" | "male";
type PoseMode = "catalog" | "custom";

type PoseCatalogItem = {
  slug: string;
  genderGroup: PoseGender;
  sourceNumber: number;
  referenceSha256: string;
};

type MaterializedPose = {
  slug: string;
  referenceSha256: string;
  asset: Asset;
};

const POSE_CATALOG: readonly PoseCatalogItem[] = [
  { slug: "male-001", genderGroup: "male", sourceNumber: 1, referenceSha256: "39f4f89ee784f02141ba081291299b58aa50be55f69032d7276292332700554f" },
  { slug: "male-002", genderGroup: "male", sourceNumber: 2, referenceSha256: "b9158080bed51302652625fe68a4e1ee37a885ff1bc42d4f4ba10e6c85be7930" },
  { slug: "male-003", genderGroup: "male", sourceNumber: 3, referenceSha256: "9dc50a389e632c871e987736d5cd391cfeef7d55f2cc953f07d6a7fe60c5fa5e" },
  { slug: "male-004", genderGroup: "male", sourceNumber: 4, referenceSha256: "5910a2038493ffced972b60694de7fd76486be6259886b4155bd9b32b462eb5c" },
  { slug: "male-005", genderGroup: "male", sourceNumber: 5, referenceSha256: "67ea448038e63d6df02b9a9636d130a921a9b6b13974b860c3f020cda8bfc93c" },
  { slug: "male-006", genderGroup: "male", sourceNumber: 6, referenceSha256: "e4029bb88cba100db579c9523e11cb92ea84bf86dc96c38e1305ccc65131fe60" },
  { slug: "male-007", genderGroup: "male", sourceNumber: 7, referenceSha256: "4d17b6d02a6c8bc8ec5034835bf25fe779d26cff6d68c2f2c9167f54c6fba762" },
  { slug: "male-008", genderGroup: "male", sourceNumber: 8, referenceSha256: "5ec7add3e4ac03edd1583ace05511f70382a5f257890f52ea4b6d98eec612f84" },
  { slug: "male-009", genderGroup: "male", sourceNumber: 9, referenceSha256: "65631fe462a273c76497c427367f63462f4dac6864bf42f628cb00357da071b9" },
  { slug: "male-010", genderGroup: "male", sourceNumber: 10, referenceSha256: "e2141932d3b090ee8a75c266c2b7653498f2260c3910fbd79435af0c4be598d4" },
  { slug: "male-011", genderGroup: "male", sourceNumber: 11, referenceSha256: "ca8bbe8379cbed21ea6a50d1293a94532050aac3a6ef9b921a9f1b4e160bc9bf" },
  { slug: "male-012", genderGroup: "male", sourceNumber: 12, referenceSha256: "c9684de194381d0ec4e784b9eb9adb1e834a796d7e1f1f64d1063f521663a39b" },
  { slug: "male-013", genderGroup: "male", sourceNumber: 13, referenceSha256: "f898a8befc54a04dcbff00945c8bce43ad50b14e5e8f2c3378beec7778679fdc" },
  { slug: "male-014", genderGroup: "male", sourceNumber: 14, referenceSha256: "366b6e9abb6f71f399c9e7bded6ddfdc7211d6648225b85730c30a7304424ad2" },
  { slug: "male-015", genderGroup: "male", sourceNumber: 15, referenceSha256: "3b3fcd2aeccb1ead7e6cdfa54ca6e015a26998353dbb0fc6fc64b215e1af8c0f" },
  { slug: "male-016", genderGroup: "male", sourceNumber: 16, referenceSha256: "1f79f81896c3d727a2c003df536d9ed01e43fd9d91efee9a2d475e0d139ab352" },
  { slug: "male-017", genderGroup: "male", sourceNumber: 17, referenceSha256: "b1200c46ddb430d770980553a8069a26c08b93b49e12b365ea737c287943f27a" },
  { slug: "male-018", genderGroup: "male", sourceNumber: 18, referenceSha256: "8bc38d734f16c5e2732e23f83c47096bcff172166be4905ca23f8b1e2f8f0e2a" },
  { slug: "male-019", genderGroup: "male", sourceNumber: 19, referenceSha256: "fee031ad1f358fb117d51392401fc39c238396edb8ae20bcea21d8bd7a66c0b5" },
  { slug: "male-020", genderGroup: "male", sourceNumber: 20, referenceSha256: "f4a3f88f9688847ea0157e59b65e7200d4c0cc86a7d518a4412677546f263ac8" },
  { slug: "male-021", genderGroup: "male", sourceNumber: 21, referenceSha256: "cd61f155f79f0ffc26c96ac013a6f55a7d858f89453a88b6cb9d2dbd5442d20f" },
  { slug: "female-001", genderGroup: "female", sourceNumber: 1, referenceSha256: "7671a682fc999cf044baf055dd4e17ac82b6160c82fc663102dab3868429d727" },
  { slug: "female-002", genderGroup: "female", sourceNumber: 2, referenceSha256: "eeaa6697e65cc0530b27f1316e85f8fa86799c946e314eedff6af4123fc9784f" },
  { slug: "female-003", genderGroup: "female", sourceNumber: 3, referenceSha256: "6b020461f189883d44112a1fe3fe33d784bb89a609c421e5b28fd405ba1190a3" },
  { slug: "female-004", genderGroup: "female", sourceNumber: 4, referenceSha256: "aa6e18f5133d64da718b9af1e6f092bf7df2c54dc41c9b64c86e8bfd3e13b185" },
  { slug: "female-005", genderGroup: "female", sourceNumber: 5, referenceSha256: "8a12e578c96ebfd2c3f12decb49d56393f661dc97cb36b5f212711657d8041ce" },
  { slug: "female-006", genderGroup: "female", sourceNumber: 6, referenceSha256: "42213ec4ebdfd8af654a18910976378ba49d50616253503d6211efd317cc745c" },
  { slug: "female-007", genderGroup: "female", sourceNumber: 7, referenceSha256: "5bc7d605d595db7ea87ea175f922e71aaa6f4023cef361e94482fd89a82943dd" },
  { slug: "female-008", genderGroup: "female", sourceNumber: 8, referenceSha256: "b2ee2fc96b30e9c17832e39d820652d6bc217677062db07f209f8daa2dbd4a5a" },
  { slug: "female-009", genderGroup: "female", sourceNumber: 9, referenceSha256: "31c70457517ce4b6fabf99e4095397a1abe4150bf5f451c8fc2b4a420ae26eb5" },
  { slug: "female-010", genderGroup: "female", sourceNumber: 10, referenceSha256: "e30d92751c717b9528dead1e84900391d3aab85e754d53952448d911055f66de" },
  { slug: "female-011", genderGroup: "female", sourceNumber: 11, referenceSha256: "a91f8a7420f95777799a93e53c62b523072ada98f21aa063b802a630106ca55e" },
  { slug: "female-012", genderGroup: "female", sourceNumber: 12, referenceSha256: "275287a8fcbc3f00810d3b22c0d467af3823014239ac89e2d403956309bd52a3" },
  { slug: "female-013", genderGroup: "female", sourceNumber: 13, referenceSha256: "b7d709c4fe57898d1b1d3d66ae43e9bf533b5722ee6dffb2c86ca8f343b3ec3b" },
  { slug: "female-014", genderGroup: "female", sourceNumber: 14, referenceSha256: "72323c69da49dba3c2c260345e83970c63d54d3f0b9172899cfcb8e6d59a978a" },
  { slug: "female-015", genderGroup: "female", sourceNumber: 15, referenceSha256: "70268c5d02080509dc85d12e1afb86ed2f53b3eaba8c1f8611eaa5f1d20ce929" },
  { slug: "female-016", genderGroup: "female", sourceNumber: 16, referenceSha256: "8559aa5da02ff3074804dce5554cdfacd81a8e237c50f7e0a999324e289da897" },
  { slug: "female-017", genderGroup: "female", sourceNumber: 17, referenceSha256: "ed5b4bcf7913a555d04dc85e49f5d3dbcf34bf44645e8841ea20792b8414b68b" },
  { slug: "female-018", genderGroup: "female", sourceNumber: 18, referenceSha256: "f6686e65bdada8b1c262feaf46ea2dfb65c80aac7a070ca7696127e8fcb01620" },
  { slug: "female-019", genderGroup: "female", sourceNumber: 19, referenceSha256: "6241edcccb3862fdabe6ea71e1b2aa17b02f3829f24c556189609d0e4cd2190a" },
  { slug: "female-020", genderGroup: "female", sourceNumber: 20, referenceSha256: "d0dee86fd8acb9d155ba299790f9c5f90ccdfa1c4e1f6780108f42ed1b2d03af" },
  { slug: "female-021", genderGroup: "female", sourceNumber: 21, referenceSha256: "f19a0e2e35ba4e9b3501eccf813e18ab273e737d86fbf254b88dbb530013b6ce" },
  { slug: "female-022", genderGroup: "female", sourceNumber: 22, referenceSha256: "be6284163c7aef01828e62f8ed6e7f140f0ba7dc438b06fbdbfae05b657cba26" },
  { slug: "female-023", genderGroup: "female", sourceNumber: 23, referenceSha256: "4974bbc7a71cbe6a71ce04936dd4de93f9a9ef9b88908c5231a73c778c906cc6" },
  { slug: "female-024", genderGroup: "female", sourceNumber: 24, referenceSha256: "011ac7c87c9f90cfb3368ced937066c428b1fc502982c0df806bebc104eb9044" },
  { slug: "female-025", genderGroup: "female", sourceNumber: 25, referenceSha256: "615005a74ff78324be83f16292e02dabf309eb5541cadf57eae9a2355eefdd53" },
  { slug: "female-026", genderGroup: "female", sourceNumber: 26, referenceSha256: "3d6d2f074dffb06f6be4856a972e20eebcb83832abc7a5df787695aa4c0ccfaf" },
  { slug: "female-027", genderGroup: "female", sourceNumber: 27, referenceSha256: "da21c8ee007e60208ebce81e9781279373213ac29db2ad12c4ba304cf33b438b" },
  { slug: "female-028", genderGroup: "female", sourceNumber: 28, referenceSha256: "acaca93426548c29ce052781730c8088a2c02437da32eb0a7a867c9a3cd2b72d" },
  { slug: "female-029", genderGroup: "female", sourceNumber: 29, referenceSha256: "c5c8d6529cc1189d16e37d523cd8b10acb400aa7c224e303784cba65e7621379" },
  { slug: "female-030", genderGroup: "female", sourceNumber: 30, referenceSha256: "8a59bb0d1069ff5b2c2d6781352489d0af5835c5b3d28d1c15663c980d1b9717" },
  { slug: "female-031", genderGroup: "female", sourceNumber: 31, referenceSha256: "f706b5e3a4188af72e844b23fa318f7bcfc87490411097c6c78a4aa04c6cabe2" },
  { slug: "female-032", genderGroup: "female", sourceNumber: 32, referenceSha256: "067bd342cc1ce71b96496378ef036526243dedc66d6bbe1d474cd93090e16bb2" },
  { slug: "female-033", genderGroup: "female", sourceNumber: 33, referenceSha256: "9fbe91580ff2cc0617b850fe5a5d3486cc57f72b98f0ed9ef892738e27658053" },
  { slug: "female-035", genderGroup: "female", sourceNumber: 35, referenceSha256: "8858bca31ac49417cd37d6d8a099fdb5a50d1ee270cacff67b0bcbcb9772ca29" },
  { slug: "female-036", genderGroup: "female", sourceNumber: 36, referenceSha256: "b3db3d66c4c52ddbf86df77afd14be2b15324dfc51093bfbf86fce0f7570c169" },
  { slug: "female-038", genderGroup: "female", sourceNumber: 38, referenceSha256: "858310c3d45d8479d4208f0e4cd9bf7c7f63054b52480006cee42050988d7740" },
  { slug: "female-039", genderGroup: "female", sourceNumber: 39, referenceSha256: "f7e4af308b2baa74f948cc4d9f7706ed7def5a4df8af8a34004f35e2875e4584" },
  { slug: "female-040", genderGroup: "female", sourceNumber: 40, referenceSha256: "fe80c8584ab700b6d87ba1812ffef3ef0b1c3fbc85bcd1b642ec4e762afcaa24" },
  { slug: "female-041", genderGroup: "female", sourceNumber: 41, referenceSha256: "077d8681181d6e617b4d99dacd421c606059874f62e8b793b470acd15f54ed8b" },
];

function poseLabel(pose: PoseCatalogItem): string {
  return `${pose.genderGroup === "female" ? "女" : "男"}姿势 ${pose.sourceNumber}`;
}

function thumbnailUrl(pose: PoseCatalogItem, width: 320 | 640): string {
  return `/images/model-pose-transfer/thumbs/${pose.slug}-${width}.webp`;
}

function previewUrl(pose: PoseCatalogItem): string {
  return `/images/model-pose-transfer/previews/${pose.slug}-960.webp`;
}

async function materializeCatalogPose(
  workspaceId: string,
  pose: PoseCatalogItem,
  idempotencyKey: string,
): Promise<Asset> {
  const result = await apiClient.POST(
    "/api/v1/workspaces/{workspace_id}/model-pose-transfer/pose-assets",
    {
      params: {
        path: { workspace_id: workspaceId },
        header: { "Idempotency-Key": idempotencyKey },
      },
      body: {
        pose_slug: pose.slug,
        reference_sha256: pose.referenceSha256,
      },
    },
  );
  return unwrapApiResponse(result);
}

function useMaterializeCatalogPose(workspaceId: string | undefined) {
  const registry = useRef<IdempotencyIntentRegistry | null>(null);
  registry.current ??= new IdempotencyIntentRegistry();

  return useMutation({
    mutationFn: async (pose: PoseCatalogItem) => {
      if (!workspaceId) throw new Error("工作区尚未就绪。");
      const signature = `model-pose-transfer:${pose.slug}:${pose.referenceSha256}`;
      const asset = await materializeCatalogPose(
        workspaceId,
        pose,
        registry.current!.keyFor(signature),
      );
      registry.current!.complete(signature);
      return { slug: pose.slug, referenceSha256: pose.referenceSha256, asset };
    },
  });
}

function AssetPreview({
  workspaceId,
  asset,
  alt,
}: {
  workspaceId?: string;
  asset?: Asset;
  alt: string;
}) {
  const access = useAssetAccessUrl(workspaceId, asset?.id);

  if (!asset) {
    return (
      <div className={styles.assetPreviewEmpty}>
        <ImageIcon size={28} />
        <span>尚未选择图片</span>
      </div>
    );
  }

  return (
    <div className={styles.assetPreview}>
      {access.data?.url ? (
        <img src={access.data.url} alt={alt} />
      ) : access.isError ? (
        <div className={styles.assetPreviewEmpty} role="alert">
          <Info size={22} />
          <span>图片预览暂时不可用</span>
        </div>
      ) : (
        <div className={styles.assetPreviewEmpty}>
          <LoaderCircle className="spin" size={22} />
          <span>正在读取图片</span>
        </div>
      )}
      <div className={styles.assetCaption}>
        <strong>{asset.original_filename}</strong>
        <span>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : "原图素材"}</span>
      </div>
    </div>
  );
}

function UploadControl({
  title,
  description,
  asset,
  pending,
  disabled,
  onFile,
  onOpenLibrary,
  uploadLabel,
}: {
  title: string;
  description: string;
  asset?: Asset;
  pending: boolean;
  disabled: boolean;
  onFile: (file: File) => void | Promise<void>;
  onOpenLibrary?: () => void;
  uploadLabel?: string;
}) {
  return (
    <ImageDropZone label={title} className={styles.uploadControl} disabled={disabled} busy={pending}
      onFiles={(files) => onFile(files[0])}>
      {({ openFileDialog, disabled: uploadDisabled, busy: uploadingFile }) => <>
        <span className={styles.uploadIcon}><Upload size={21} /></span>
        <div className={styles.uploadCopy}>
          <strong>{title}</strong>
          <p>{asset?.original_filename ?? description}</p>
          <small>{IMAGE_UPLOAD_FORMAT_HINT}</small>
        </div>
        <div className={styles.uploadActions}>
          <button
            type="button"
            disabled={uploadDisabled}
            onClick={openFileDialog}
          >
            {uploadingFile ? <LoaderCircle className="spin" size={15} /> : <Upload size={15} />}
            {uploadLabel ?? (asset ? "重新上传" : "本地上传")}
          </button>
          {onOpenLibrary ? (
            <button type="button" disabled={disabled || uploadingFile} onClick={onOpenLibrary}>
              <Images size={15} /> 从素材库选择
            </button>
          ) : null}
        </div>
      </>}
    </ImageDropZone>
  );
}

export function ModelPoseTransferWorkspace() {
  const feature = getFeatureById("model-pose-transfer");
  const { workspace, isPending: workspacePending, error: workspaceError } = useWorkspace();
  const sourceUpload = useUploadAsset(workspace?.id);
  const customPoseUpload = useUploadAsset(workspace?.id);
  const materializePose = useMaterializeCatalogPose(workspace?.id);
  const createJob = useCreateJob(workspace?.id);
  const runtime = useRuntimeStatus();
  const capability = resolveRuntimeCapability(
    runtime.data,
    feature?.runtimeCapability ?? "model_pose_transfer",
  );
  const workflow = presentWorkflow(capability);

  const [jobId, setJobId] = useWorkspaceJobId("/workspace/model-pose-transfer");
  const job = useJob(workspace?.id, jobId);
  const saved = workspaceJobSnapshot(job.data);
  const restoredSource = useAsset(workspace?.id, jobId ? saved.id("model_source_asset_id") : undefined);
  const restoredPose = useAsset(workspace?.id, jobId ? saved.id("pose_reference_asset_id") : undefined);
  const [draftModelSource, setModelSource] = useState<Asset>();
  const [sourceLibraryOpen, setSourceLibraryOpen] = useState(false);
  const [draftPoseMode, setPoseMode] = useState<PoseMode>("catalog");
  const [draftGender, setGender] = useState<PoseGender>("female");
  const [draftSelectedPoseSlug, setSelectedPoseSlug] = useState<string>();
  const [draftCustomPose, setCustomPose] = useState<Asset>();
  const [materializedPose, setMaterializedPose] = useState<MaterializedPose>();
  const [draftInstruction, setInstruction] = useState("");
  const [uiError, setUiError] = useState<string>();
  const [humanReviewComplete, setHumanReviewComplete] = useWorkspaceJobReview(jobId);
  const submissionLock = useRef(false);

  const modelSource = jobId ? restoredSource.data : draftModelSource;
  const customPose = jobId ? restoredPose.data : draftCustomPose;
  const selectedPoseSlug = jobId ? saved.id("pose_slug") : draftSelectedPoseSlug;
  const poseMode = jobId ? (selectedPoseSlug ? "catalog" : "custom") : draftPoseMode;
  const gender = jobId ? POSE_CATALOG.find((pose) => pose.slug === selectedPoseSlug)?.genderGroup ?? "female" : draftGender;
  const instruction = jobId ? saved.text("instruction") : draftInstruction;
  const resultAssets = selectJobResultAssets(job.data);
  const visiblePoses = useMemo(
    () => POSE_CATALOG.filter((pose) => pose.genderGroup === gender),
    [gender],
  );
  const selectedPose = useMemo(
    () => POSE_CATALOG.find((pose) => pose.slug === selectedPoseSlug),
    [selectedPoseSlug],
  );

  const isSubmitting = materializePose.isPending || createJob.isPending;
  const canRestartTask = Boolean(
    jobId && job.data && ["partial", "completed", "failed", "cancelled"].includes(job.data.status),
  );
  const sourceSelectionLocked = !workspace || isSubmitting || Boolean(jobId && !canRestartTask);
  const selectedPoseReady = poseMode === "catalog" ? Boolean(selectedPose) : Boolean(customPose);
  const runtimeAllowsGeneration = workflow.canSubmit && workflow.canDeliverImages;
  const canSubmit = Boolean(
    workspace?.id &&
    modelSource &&
    selectedPoseReady &&
    runtimeAllowsGeneration &&
    !workspacePending &&
    !sourceUpload.isPending &&
    !customPoseUpload.isPending &&
    !isSubmitting &&
    !jobId,
  );

  const requestError =
    workspaceError ??
    restoredSource.error ?? restoredPose.error ??
    runtime.error ??
    sourceUpload.error ??
    customPoseUpload.error ??
    materializePose.error ??
    createJob.error ??
    job.error;

  const uploadModelSource = async (file: File) => {
    setUiError(undefined);
    try {
      const asset = await sourceUpload.mutateAsync({ file, mediaKind: "image" });
      setModelSource(asset);
      if (customPose?.id === asset.id) setCustomPose(undefined);
    } catch {
      // The mutation error is rendered in the sticky status bar.
    }
  };

  const uploadCustomPose = async (file: File) => {
    setUiError(undefined);
    try {
      const asset = await customPoseUpload.mutateAsync({ file, mediaKind: "image" });
      if (asset.id === modelSource?.id) {
        setUiError("模特原图和姿势参考图不能使用同一张素材。");
        return;
      }
      setCustomPose(asset);
      setSelectedPoseSlug(undefined);
      setMaterializedPose(undefined);
    } catch {
      // The mutation error is rendered in the sticky status bar.
    }
  };

  const choosePoseMode = (mode: PoseMode) => {
    if (jobId) return;
    setPoseMode(mode);
    setUiError(undefined);
    if (mode === "catalog") {
      setCustomPose(undefined);
    } else {
      setSelectedPoseSlug(undefined);
      setMaterializedPose(undefined);
    }
  };

  const chooseCatalogPose = (pose: PoseCatalogItem) => {
    if (jobId || isSubmitting) return;
    setPoseMode("catalog");
    setSelectedPoseSlug(pose.slug);
    setCustomPose(undefined);
    setMaterializedPose((current) => current?.slug === pose.slug ? current : undefined);
    setUiError(undefined);
  };

  const submit = async () => {
    if (!canSubmit || !modelSource || submissionLock.current) return;
    submissionLock.current = true;
    let submitted = false;
    setUiError(undefined);
    setHumanReviewComplete(false);

    try {
      let poseReferenceAsset: Asset;
      if (poseMode === "catalog") {
        if (!selectedPose) throw new Error("请选择一个内置姿势。");
        const cached =
          materializedPose?.slug === selectedPose.slug &&
          materializedPose.referenceSha256 === selectedPose.referenceSha256
            ? materializedPose
            : undefined;
        const resolved = cached ?? await materializePose.mutateAsync(selectedPose);
        if (!cached) setMaterializedPose(resolved);
        poseReferenceAsset = resolved.asset;
      } else {
        if (!customPose) throw new Error("请上传自定义姿势参考图。");
        poseReferenceAsset = customPose;
      }

      if (poseReferenceAsset.id === modelSource.id) {
        throw new Error("模特原图和姿势参考图不能使用同一张素材。");
      }

      const body = {
        type: "model_pose_transfer",
        intent: "submit",
        display_name: "模特换姿势",
        input: {
          model_source_asset_id: modelSource.id,
          pose_reference_asset_id: poseReferenceAsset.id,
          ...(selectedPose && poseMode === "catalog"
            ? {
                pose_slug: selectedPose.slug,
                pose_reference_sha256: selectedPose.referenceSha256,
              }
            : {}),
        },
        configuration: {
          aspect_ratio: "follow-source",
          image_count: 1,
          quality: "high",
          output_format: "png",
          instruction: instruction.trim(),
        },
      } satisfies JobCreate;

      const created = await createJob.mutateAsync(body);
      submitted = true;
      setJobId(created.id);
    } catch (error) {
      setUiError(errorMessage(error));
    } finally {
      if (!submitted) submissionLock.current = false;
    }
  };

  const resetTask = () => {
    submissionLock.current = false;
    setJobId(undefined);
    setModelSource(undefined);
    setSourceLibraryOpen(false);
    setSelectedPoseSlug(undefined);
    setCustomPose(undefined);
    setMaterializedPose(undefined);
    setInstruction("");
    setUiError(undefined);
    setHumanReviewComplete(false);
    sourceUpload.reset();
    customPoseUpload.reset();
    createJob.reset();
    materializePose.reset();
  };

  const progressText = job.data
    ? `${jobNote(job.data)} · ${Math.round(job.data.progress)}%`
    : "预计生成 1 张图片";

  if (!feature) {
    return (
      <AppShell flush sidebar={false}>
        <div className={styles.workspacePage}><p role="alert">功能配置缺失。</p></div>
      </AppShell>
    );
  }

  return (
    <WorkspaceJobGate jobId={jobId} job={job.data} error={job.error} onRetry={() => void job.refetch()} expectedType="model_pose_transfer">
    <AppShell flush sidebar={false}>
      <div className={styles.workspacePage}>
        <WorkspaceHeader
          jobId={jobId}
          eyebrow="AI 商品图 · 服装模特"
          title={feature.title}
          description={feature.description}
          meta={
            <span className={styles.safetyBadge}>
              <ShieldCheck size={15} /> 原图生成 · 姿势精准参考
            </span>
          }
        />

        <div className={styles.stepBar} aria-label="模特换姿势步骤">
          <span data-active><i>01</i>上传模特原图</span>
          <span data-active={Boolean(modelSource) || undefined}><i>02</i>选择目标姿势</span>
          <span data-active={Boolean(jobId) || undefined}><i>03</i>生成与检查</span>
          <span data-active={Boolean(resultAssets.length) || undefined}><i>04</i>下载结果</span>
        </div>

        <JobProgressFeedback pending={createJob.isPending && !jobId} />

        <div className={styles.workspaceGrid}>
          <main className={styles.mainColumn}>
            <section className={styles.panel}>
              <div className={styles.sectionHeading}>
                <div><span>01</span><div><h2>上传模特原图</h2><p>请使用服装完整、人物清晰、无遮挡的原始图片。</p></div></div>
                <strong>必填</strong>
              </div>
              <div className={styles.sourceGrid}>
                <UploadControl
                  title="模特服装原图"
                  description="上传本地图片，或从个人素材库中选择"
                  asset={modelSource}
                  pending={sourceUpload.isPending}
                  disabled={sourceSelectionLocked}
                  uploadLabel={canRestartTask ? "重新上传并新建任务" : undefined}
                  onFile={(file) => {
                    if (canRestartTask) resetTask();
                    return uploadModelSource(file);
                  }}
                  onOpenLibrary={() => setSourceLibraryOpen(true)}
                />
                <AssetPreview workspaceId={workspace?.id} asset={modelSource} alt="模特服装原图预览" />
              </div>
            </section>

            <section className={styles.panel}>
              <div className={styles.sectionHeading}>
                <div><span>02</span><div><h2>选择目标姿势</h2><p>网格展示清晰缩略图；正式生成只使用对应高清原图。</p></div></div>
                <strong>单选</strong>
              </div>

              <div className={styles.modeTabs} role="tablist" aria-label="姿势来源">
                <button
                  type="button"
                  role="tab"
                  aria-selected={poseMode === "catalog"}
                  data-active={poseMode === "catalog" || undefined}
                  disabled={Boolean(jobId) || isSubmitting}
                  onClick={() => choosePoseMode("catalog")}
                >
                  <Images size={16} /> 内置姿势库
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={poseMode === "custom"}
                  data-active={poseMode === "custom" || undefined}
                  disabled={Boolean(jobId) || isSubmitting}
                  onClick={() => choosePoseMode("custom")}
                >
                  <Upload size={16} /> 自定义姿势
                </button>
              </div>

              {poseMode === "catalog" ? (
                <div className={styles.catalogArea}>
                  <div className={styles.catalogToolbar}>
                    <div role="group" aria-label="筛选模特性别">
                      <button
                        type="button"
                        data-active={gender === "female" || undefined}
                        aria-pressed={gender === "female"}
                        disabled={Boolean(jobId) || isSubmitting}
                        onClick={() => setGender("female")}
                      >女模姿势 <span>39</span></button>
                      <button
                        type="button"
                        data-active={gender === "male" || undefined}
                        aria-pressed={gender === "male"}
                        disabled={Boolean(jobId) || isSubmitting}
                        onClick={() => setGender("male")}
                      >男模姿势 <span>21</span></button>
                    </div>
                    <p>已显示 {visiblePoses.length} 个姿势</p>
                  </div>
                  <div className={styles.poseGrid}>
                    {visiblePoses.map((pose) => {
                      const selected = pose.slug === selectedPoseSlug;
                      return (
                        <button
                          key={pose.slug}
                          type="button"
                          className={styles.poseCard}
                          data-selected={selected || undefined}
                          aria-pressed={selected}
                          aria-label={`选择${poseLabel(pose)}`}
                          disabled={Boolean(jobId) || isSubmitting}
                          onClick={() => chooseCatalogPose(pose)}
                        >
                          <span className={styles.poseImageFrame}>
                            <img
                              src={thumbnailUrl(pose, 320)}
                              srcSet={`${thumbnailUrl(pose, 320)} 320w, ${thumbnailUrl(pose, 640)} 640w`}
                              sizes="(max-width: 640px) 44vw, (max-width: 1100px) 22vw, 150px"
                              loading="lazy"
                              decoding="async"
                              alt={poseLabel(pose)}
                            />
                            {selected ? <i><Check size={15} /></i> : null}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <div className={styles.customPoseArea}>
                  <UploadControl
                    title="自定义姿势参考图"
                    description="上传一张动作清晰、全身完整的姿势图片"
                    asset={customPose}
                    pending={customPoseUpload.isPending}
                    disabled={!workspace || Boolean(jobId) || isSubmitting}
                    onFile={(file) => uploadCustomPose(file)}
                  />
                  <AssetPreview workspaceId={workspace?.id} asset={customPose} alt="自定义姿势参考图预览" />
                </div>
              )}
            </section>

            <section className={styles.panel}>
              <div className={styles.sectionHeading}>
                <div><span>03</span><div><h2>补充要求</h2><p>可选。仅补充服装保真、表情或背景要求，不要改写目标姿势。</p></div></div>
                <strong>可选</strong>
              </div>
              <textarea
                className={styles.instruction}
                value={instruction}
                maxLength={500}
                disabled={Boolean(jobId) || isSubmitting}
                placeholder="例如：保持服装版型、花纹和配饰不变，人物表情自然。"
                onChange={(event) => setInstruction(event.currentTarget.value)}
              />
              <div className={styles.textareaMeta}><span>源图画布优先，默认输出 PNG</span><span>{instruction.length} / 500</span></div>
            </section>

            {jobId ? (
              <section className={styles.panel}>
                <div className={styles.sectionHeading}>
                  <div><span>04</span><div><h2>任务进度</h2><p>任务会自动刷新，完成后可直接查看和下载。</p></div></div>
                  {job.data && <StatusBadge status={job.data.status} />}
                </div>
                <JobProgressFeedback job={job.data} error={job.error} lastSyncedAt={job.dataUpdatedAt}
                  refreshing={job.isFetching} onRefresh={() => void job.refetch()} />
              </section>
            ) : null}

            <ResultGrid
              workspaceId={workspace?.id}
              assets={resultAssets}
              expectedCount={1}
              title="模特换姿势结果"
              archiveJobId={
                job.data?.status === "completed" && humanReviewComplete
                  ? job.data.id
                  : undefined
              }
            />
            {job.data?.status === "completed" && resultAssets.length > 0 ? (
              <SkillReviewChecklist key={jobId}
                items={[
                  { key: "roles", label: "没有发生人物身份、服装或场景串位，姿势参考图没有泄漏外观。" },
                  { key: "identity", label: "原图人物、自然比例、服装、鞋饰、背景和光线仍保持可识别。" },
                  { key: "geometry", label: "头、躯干、四肢、脚部、承重腿、跨步和左右关系符合目标姿势。" },
                  { key: "anatomy", label: "没有拉伸肢体、断裂关节、重复肢体、悬空脚或多余人物。" },
                  { key: "canvas", label: "画布、裁切和背景保持源图一致，并已记录仍可见的偏差。" },
                ]}
                onCompleteChange={setHumanReviewComplete}
              />
            ) : null}
          </main>

          <aside className={styles.previewColumn}>
            <section className={styles.previewPanel}>
              <div className={styles.previewHeading}>
                <span><UserRound size={17} /></span>
                <div><h2>姿势预览</h2></div>
              </div>
              {poseMode === "catalog" && selectedPose ? (
                <div className={styles.posePreview}>
                  <img src={previewUrl(selectedPose)} alt={`${poseLabel(selectedPose)}高清预览`} />
                  <div><strong>{poseLabel(selectedPose)}</strong><span>{selectedPose.slug}</span></div>
                </div>
              ) : poseMode === "custom" && customPose ? (
                <AssetPreview workspaceId={workspace?.id} asset={customPose} alt="自定义姿势清晰预览" />
              ) : (
                <div className={styles.previewEmpty}>
                  <UserRound size={38} />
                  <strong>选择一个目标姿势</strong>
                  <p>内置姿势会先显示缩略图，选中后加载清晰预览。</p>
                </div>
              )}
            </section>
          </aside>
        </div>

        {sourceLibraryOpen ? (
          <AssetLibraryDialog
            workspaceId={workspace?.id}
            title="选择模特服装原图"
            description="选择人物和服装都清晰完整的原始图片。"
            selectedIds={canRestartTask ? [] : modelSource ? [modelSource.id] : []}
            excludedIds={customPose ? [customPose.id] : []}
            maxItems={1}
            disabled={sourceSelectionLocked}
            onClose={() => setSourceLibraryOpen(false)}
            onConfirm={(assetIds, assets) => {
              const nextSource = assets.find((asset) => asset.id === assetIds[0]);
              if (!nextSource) {
                setSourceLibraryOpen(false);
                return;
              }
              if (canRestartTask) resetTask();
              setModelSource(nextSource);
              setSourceLibraryOpen(false);
              setUiError(undefined);
            }}
          />
        ) : null}

        <footer className={styles.stickyBar}>
          <div className={styles.stickyContext}>
            <span>{workspacePending ? <LoaderCircle className="spin" size={17} /> : <Clock3 size={17} />}</span>
            <p><strong>{workspace?.name ?? "正在连接工作区"}</strong><small>{jobId ? progressText : "固定生成 1 张高清结果"}</small>{!jobId && <CreditCostHint jobType="model_pose_transfer" count={1} />}</p>
          </div>
          <div className={styles.stickyActions}>
            {(uiError || requestError) ? (
              <p role="alert"><Info size={15} />{uiError ?? errorMessage(requestError)}</p>
            ) : !runtimeAllowsGeneration ? null
            : !modelSource ? (
              <p><Info size={15} />请先上传模特服装原图。</p>
            ) : !selectedPoseReady ? (
              <p><Info size={15} />请选择内置姿势或上传自定义姿势。</p>
            ) : (
              <p><CheckCircle2 size={15} />原图和目标姿势已就绪。</p>
            )}
            {jobId ? (
              <button type="button" className={styles.secondaryAction} onClick={resetTask}>
                <RefreshCcw size={16} /> 新建任务
              </button>
            ) : (
              <button type="button" className={styles.primaryAction} disabled={!canSubmit} onClick={() => void submit()}>
                {isSubmitting ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}
                {materializePose.isPending ? "正在准备高清姿势" : createJob.isPending ? "正在创建任务" : "生成换姿势结果"}
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
