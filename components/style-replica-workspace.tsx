"use client";

import { GenerationRecommendation } from "@/components/generation-recommendation";

import { CreditCostHint } from "@/features/billing/components/credit-account";

import Link from "next/link";
import { WorkspaceVisualPreview } from "./workspace-visual-preview";
import { useEffect, useRef, useState, type RefObject } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { formatBeijingDate } from "@/lib/datetime.mjs";
import { Check, ChevronRight, CircleHelp, Download, History, ImagePlus, LoaderCircle, Plus, RotateCcw, Sparkles, Upload, X } from "lucide-react";
import { useWorkspace } from "@/features/workspaces/api/workspaces";
import { usePreviewVisibility } from "@/features/assets/hooks/use-preview-visibility";
import { ImageDropZone } from "@/components/image-drop-zone";
import { validateImageUpload } from "@/lib/image-upload";
import { getAssetAccessUrl, useAssetAccessUrl, useUploadAsset } from "@/features/assets/api/assets";
import { useJob } from "@/features/jobs/api/jobs";
import { isLocalDevelopmentAuthEnabled, LOCAL_DEV_AUTH_USER_ID } from "@/lib/auth/local-dev";
import { probeReferenceImage } from "@/lib/replica/reference-images";
import { fastReplicaOutputAsset } from "@/lib/replica/result-assets";
import { useBodyScrollLock } from "@/lib/hooks/use-body-scroll-lock";
import { mergeReplicaProgress, replicaRetryNeedsReconciliation, replicaAnalysisMessage, replicaOverallProgress, replicaPlanFailure, replicaSegmentationFailure, replicaWorkflowStep } from "@/lib/replica/progress";
import { ReplicaSelect } from "./replica-select";
import { FormDisclosure } from "./form-disclosure";
import styles from "./style-replica-workspace.module.css";

type AssetChoice = { id: string; name: string };
type Product = { title: string | null; platform: string; targetPlatform?: string; imageUrls: string[]; descImageUrls: string[]; skuImageUrls: string[] };
type Plan = { reference: { asset_id: string; title: string; aspect_ratio: string }; analysis: Record<string, unknown>; prompt: string };
type ExecutionProgress = { post_generation_review?: "disabled"; generated?: number; percentage: number; total: number; reviewed: number; accepted: number; rejected: number; current_module: number; phase: string; modules: { id: string; status: string; attempts: number; reasons: string[] }[] };
type ReplicaDocument = { id: string; kind: string; status: string; created_at: string; updated_at?: string; payload: { segmentation_restart_available?: boolean; segmentation_repair_available?: boolean; segmentation_diagnostics?: { coverage_valid: boolean; columns: { reference_index: number | null; split_count: number; inventory_count: number; reason?: string }[] }; execution_resume_available?: boolean; error_code?: string; analysis_attempt_id?: string; analysis_resume_available?: boolean; analysis_revision_available?: boolean; analysis_progress?: { stage: string; total: number; planned: number; reviewed: number; accepted: number; pending_corrections: number }; execution_progress?: ExecutionProgress; auto_start_after_plan?: boolean; automatic_submission_error?: string; replica_speed?: "standard" | "fast"; replication_method?: "exact" | "style"; analysis_stage?: string; skill_id?: string; product?: Product; product_asset_ids?: string[]; references?: Plan["reference"][]; plans?: Plan[]; plan_hash?: string; job_ids?: string[]; progress?: number; error?: string; facts?: string; confirmed_instructions?: string[]; submission_complete?: boolean; platform_code?: string; language?: string } };
const STEPS = ["选图", "分析中", "商品信息", "后台规划", "生成中", "完成"];

async function replicaRequest<T>(workspaceId: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (isLocalDevelopmentAuthEnabled()) headers["X-User-Id"] = LOCAL_DEV_AUTH_USER_ID;
  const response = await fetch(`/api/v1/workspaces/${workspaceId}/replica${path}`, { method: body === undefined ? "GET" : "POST", headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "include", signal });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message ?? result.detail?.message ?? result.message ?? "服务暂时不可用，请稍后再试。");
  return result;
}


function analysisText(value: unknown): string { if (typeof value === "string") return value; if (Array.isArray(value)) return value.map(analysisText).filter(Boolean).slice(0, 3).join(" · ").slice(0, 220); if (value && typeof value === "object") return analysisText(Object.values(value)); return ""; }

function AssetImage({ workspaceId, assetId, className, onClick }: { workspaceId: string; assetId: string; className?: string; onClick?: (url: string) => void }) {
  const { ref, visible } = usePreviewVisibility<HTMLElement>();
  const access = useAssetAccessUrl(workspaceId, visible ? assetId : undefined, "thumbnail");
  const url = access.data?.url;
  return url ? <img ref={ref as RefObject<HTMLImageElement | null>} className={className} src={url} loading="lazy" decoding="async" alt="商品图片" onClick={() => onClick?.(access.data?.original_url ?? url)} /> : <span ref={ref as RefObject<HTMLSpanElement | null>} className={styles.imagePlaceholder}><ImagePlus size={22} /></span>;

}

function ResultCard({ workspaceId, jobId, sessionId, outputIndex = 0, title, onPreview, onStatus, onRetry, retrying, retryWhole = false, resumeWhole = false, fastSuite = false, moduleProgress }: { workspaceId: string; jobId: string; sessionId: string; outputIndex?: number; title: string; onPreview: (url: string) => void; onStatus: (id: string, status: string) => void; onRetry: (id: string) => void; retrying: boolean; retryWhole?: boolean; resumeWhole?: boolean; fastSuite?: boolean; moduleProgress?: ExecutionProgress["modules"][number] }) {
  const job = useJob(workspaceId, jobId);
  const [error, setError] = useState("");
  const data = job.data;
  const needsReconciliation = replicaRetryNeedsReconciliation(data);
  const asset = fastSuite && data ? fastReplicaOutputAsset(data.items, data.assets, outputIndex) : data?.assets.filter(item => item.direction === "output" && item.is_deliverable)[outputIndex];
  const terminal = ["completed", "partial", "failed", "cancelled"].includes(data?.status ?? "");
  const moduleLabel = terminal && needsReconciliation ? "原任务结果待核查" : moduleProgress?.status === "GENERATED" ? "本屏已生成，等待整组交付" : moduleProgress?.status === "AWAITING_ARTWORK" ? "正在生成本屏" : moduleProgress?.status === "COMPLETE" ? (terminal ? "本屏审图通过，整组暂未交付" : "本屏审图通过，等待整组交付") : moduleProgress?.status === "REJECTED_BEST_EFFORT" ? "本屏尚未通过审图" : moduleProgress?.status === "REPAIR_REQUIRED" ? "正在修正本屏" : moduleProgress?.attempts ? "正在生成与审查本屏" : "等待处理本屏";
  useEffect(() => { if (data) onStatus(jobId, data.status); }, [data, jobId, onStatus]);
  const download = async () => { try { if (!asset) return; const access = await getAssetAccessUrl(workspaceId, asset.asset_id, "attachment"); const response = await fetch(access.url, { credentials: "include" }); if (!response.ok) throw new Error("图片下载失败，请稍后重试。"); const url = URL.createObjectURL(await response.blob()); const a = document.createElement("a"); a.href = url; a.download = `${title}.png`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); } catch (caught) { setError(caught instanceof Error ? caught.message : "下载失败"); } };
  return <article className={styles.resultCard}>
    <div className={styles.resultMedia}>{asset ? <AssetImage workspaceId={workspaceId} assetId={asset.asset_id} onClick={onPreview} /> : <div className={styles.generating}>{terminal ? <CircleHelp size={26} /> : <LoaderCircle className="spin" size={28} />}<span>{moduleProgress ? moduleLabel : terminal ? "本张暂未完成" : "正在绘制你的商品"}</span><small>{terminal ? (needsReconciliation ? "原生图请求结果尚未核实，请先找回原任务，避免重复生成。" : resumeWhole ? "已保留当前进度，可继续处理剩余步骤。" : data?.error_message ?? data?.items.find(item => item.error_message)?.error_message) : `${data?.progress ?? 0}% · 可离开页面，稍后在记录中查看`}</small></div>}</div>
    {!!moduleProgress?.reasons.length && <details className={styles.auditReasons}><summary>查看本屏审图原因</summary><ul>{moduleProgress.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></details>}
    <div className={styles.resultMeta}><strong>{title}</strong><span>{data?.status === "completed" ? "已完成" : needsReconciliation ? "待核查" : terminal ? "需重试" : "生成中"}</span></div>
    <div className={styles.resultActions}>{asset && <button onClick={() => void download()}><Download size={14} />下载</button>}{terminal && !asset && (outputIndex === 0 || fastSuite) && <button disabled={retrying || needsReconciliation} onClick={() => onRetry(jobId)}><RotateCcw size={13} />{needsReconciliation ? "原任务待核查" : retrying ? "正在提交" : resumeWhole ? "继续处理剩余步骤" : fastSuite ? "重试未完成图片" : retryWhole ? "重新复刻整组" : "重新生成"}</button>}<Link href={`/style-replica?sessionId=${encodeURIComponent(sessionId)}`}>查看任务<ChevronRight size={13} /></Link></div>
    {error && <p className={styles.error}>{error}</p>}
  </article>;
}

export function StyleReplicaWorkspace() {
  const queryClient = useQueryClient();
  const { workspace } = useWorkspace();
  const workspaceId = workspace?.id;
  const upload = useUploadAsset(workspaceId);
  const uploadBatchLock = useRef(false);
  const [mode, setMode] = useState<"link" | "image">("link");
  const [replicaSpeed, setReplicaSpeed] = useState<"standard" | "fast">("standard");
  const [replicationMethod, setReplicationMethod] = useState<"exact" | "style">("exact");
  const [products, setProducts] = useState<AssetChoice[]>([]);
  const [references, setReferences] = useState<AssetChoice[]>([]);
  const [sourcePlatform, setSourcePlatform] = useState("auto");
  const [sourcePlatforms, setSourcePlatforms] = useState<{ code: string; label: string }[]>([]);
  const [url, setUrl] = useState("");
  const [productDoc, setProductDoc] = useState<ReplicaDocument>();
  const [sessionDoc, setSessionDoc] = useState<ReplicaDocument>();
  const [selected, setSelected] = useState<string[]>([]);
  const [hiddenSources, setHiddenSources] = useState<string[]>([]);
  const [imageTab, setImageTab] = useState<"imageUrls" | "descImageUrls" | "skuImageUrls">("imageUrls");
  const [platform, setPlatform] = useState("auto");
  const [language, setLanguage] = useState("zh-CN");
  const [mainRatio, setMainRatio] = useState("1:1");
  const [detailRatio, setDetailRatio] = useState("3:4");
  const [ratio, setRatio] = useState("follow-reference");
  const [selectedReferences, setSelectedReferences] = useState<string[]>([]);
  const actionLock = useRef(false);
  const draftGeneration = useRef(0);
  const retryIntents = useRef<Record<string, string>>({});
  const [facts, setFacts] = useState("");
  const [analysisRequestId, setAnalysisRequestId] = useState("");
  const [instructions, setInstructions] = useState<string[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [modal, setModal] = useState<"history" | "help" | "fees" | null>(null);
  const [history, setHistory] = useState<ReplicaDocument[]>([]);
  const [preview, setPreview] = useState("");
  useBodyScrollLock(Boolean(modal || preview));
  const [jobStatuses, setJobStatuses] = useState<Record<string, string>>({});
  const [hydratedWorkspace, setHydratedWorkspace] = useState("");
  const draftWorkspace = useRef("");
  const selectedReferenceCount = references.filter(item => selectedReferences.includes(item.id)).length;
  const status = sessionDoc?.status;
  const planFailure = sessionDoc ? replicaPlanFailure(sessionDoc) : undefined;
  const segmentationFailure = sessionDoc ? replicaSegmentationFailure(sessionDoc) : undefined;
  const working = busy !== "" || ["analysing"].includes(status ?? "") || productDoc?.status === "fetching";
  const product = productDoc?.payload.product;
  useEffect(() => {
    if (!workspaceId) return;
    const controller = new AbortController();
    void replicaRequest<{ code: string; label: string }[]>(workspaceId, "/product-platforms", undefined, controller.signal)
      .then(setSourcePlatforms).catch(() => {});
    return () => controller.abort();
  }, [workspaceId]);
  const sourceImages = (key: "imageUrls" | "descImageUrls" | "skuImageUrls") => (product?.[key] ?? []).filter(src => !hiddenSources.includes(src));
  const sourcePreview = (src: string) => {
    const group = (["imageUrls", "descImageUrls", "skuImageUrls"] as const).find(key => product?.[key].includes(src));
    return workspaceId && productDoc && group ? `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/replica/products/${encodeURIComponent(productDoc.id)}/images/${group}/${product![group].indexOf(src)}` : "";
  };
  const activeImageTab = sourceImages(imageTab).length ? imageTab : (["imageUrls", "descImageUrls", "skuImageUrls"] as const).find(key => sourceImages(key).length) ?? imageTab;
  const hideSource = (src: string) => { setHiddenSources(current => current.includes(src) ? current : [...current, src]); setSelected(current => current.filter(value => value !== src)); };
  const selectReplicationMethod = (value: "exact" | "style") => {
    if (working || actionLock.current || value === replicationMethod) return;
    window.history.replaceState(null, "", "/style-replica");
    setReplicationMethod(value); setAnalysisRequestId(crypto.randomUUID());
    setSessionDoc(undefined); setInstructions([]); setJobStatuses({}); setError("");
    retryIntents.current = {};
  };

  const selectReplicaSpeed = (value: "standard" | "fast") => {
    if (working || actionLock.current || value === replicaSpeed) return;
    window.history.replaceState(null, "", "/style-replica");
    setReplicaSpeed(value); setAnalysisRequestId(crypto.randomUUID());
    setSessionDoc(undefined); setInstructions([]); setJobStatuses({}); setError("");
    retryIntents.current = {};
  };

  const plans = sessionDoc?.payload.plans ?? [];
  const native = !!sessionDoc?.payload.replication_method;
  const jobs = sessionDoc?.payload.job_ids ?? [];
  const complete = jobs.length > 0 && sessionDoc?.payload.submission_complete !== false && jobs.every(id => ["completed", "partial", "failed", "cancelled"].includes(jobStatuses[id]));
  const step = replicaWorkflowStep(sessionDoc, jobStatuses);
  const generationStepLabel = status === "failed" ? "待修正" : status === "partial" ? "部分完成" : status === "cancelled" ? "已取消" : "生成中";
  const reportStatus = useState(() => (id: string, value: string) => setJobStatuses(current => current[id] === value ? current : { ...current, [id]: value }))[0];
  const executionResume = native && (sessionDoc?.payload.execution_resume_available || sessionDoc?.payload.error_code === "REPLICA_ANALYSIS_BUDGET_REACHED");
  const executionProgress = sessionDoc?.payload.execution_progress;
  const directDelivery = executionProgress?.post_generation_review === "disabled";
  const executionHeading = status === "failed" ? (executionResume ? "已完成步骤已保留" : "本组尚未通过验收") : status === "partial" ? "部分图片尚未完成交付" : status === "cancelled" ? "本组已取消" : status === "completed" ? "整组已完成交付" : executionProgress && (["REPAIR_READY", "REPAIR_PREPARED"].includes(executionProgress.phase) ? `正在修正第 ${executionProgress.current_module} 屏` : `正在处理第 ${executionProgress.current_module} / ${executionProgress.total} 屏`);
  const overallProgress = sessionDoc ? replicaOverallProgress(sessionDoc) : 0;
  const pendingDoc = productDoc?.status === "fetching" ? productDoc : ["analysing", "generating"].includes(sessionDoc?.status ?? "") || (sessionDoc?.status === "awaiting_confirmation" && sessionDoc.payload.auto_start_after_plan && !sessionDoc.payload.automatic_submission_error) ? sessionDoc : null;
  const pendingId = pendingDoc?.id;
  const pendingKind = pendingDoc?.kind;

  useEffect(() => {
    if (!workspaceId || draftWorkspace.current === workspaceId) return;
    let active = true;
    const generation = draftGeneration.current;
    queueMicrotask(() => {
      if (!active || generation !== draftGeneration.current) return;
      draftWorkspace.current = workspaceId;
      setSessionDoc(undefined); setProductDoc(undefined); setSelected([]); setInstructions([]); setJobStatuses({});
      try { const saved = JSON.parse(localStorage.getItem(`polaris-replica:${workspaceId}`) ?? "{}"); setProducts(saved.products ?? []); setReferences(saved.references ?? []); setAnalysisRequestId(saved.analysisRequestId ?? crypto.randomUUID()); setUrl(saved.url ?? ""); setSourcePlatform(saved.sourcePlatform ?? "auto"); setImageTab(saved.imageTab ?? "imageUrls"); if (saved.productDocId) void replicaRequest<ReplicaDocument>(workspaceId, `/documents/${saved.productDocId}`).then(value => { if (active && generation === draftGeneration.current) { setProductDoc(value); setSelected(saved.selected ?? []); } }).catch(() => {}); setMode(saved.mode ?? "link"); setReplicationMethod(saved.replicationMethod ?? "exact"); setReplicaSpeed(saved.replicaSpeed === "fast" ? "fast" : "standard"); setFacts(saved.facts ?? ""); setPlatform(saved.platform ?? "auto"); setLanguage(saved.language ?? "zh-CN"); setMainRatio(saved.mainRatio ?? "1:1"); setDetailRatio(saved.detailRatio ?? "3:4"); setRatio(saved.ratio ?? "follow-reference"); setSelectedReferences(saved.selectedReferences ?? (saved.references ?? []).map((item: AssetChoice) => item.id)); if (saved.sessionId && !new URLSearchParams(window.location.search).has("sessionId") && !new URLSearchParams(window.location.search).has("jobId")) void replicaRequest<ReplicaDocument>(workspaceId, `/documents/${saved.sessionId}`).then(value => { if (active && generation === draftGeneration.current) { setSessionDoc(value); setReplicaSpeed(value.payload.replica_speed === "fast" ? "fast" : "standard"); } }).catch(() => {}); } catch { /* A stale draft must not block the workspace. */ }
      setHydratedWorkspace(workspaceId);
    });
    return () => { active = false; };
  }, [workspaceId]);
  useEffect(() => { if (workspaceId && hydratedWorkspace === workspaceId) localStorage.setItem(`polaris-replica:${workspaceId}`, JSON.stringify({ products, references, mode, replicationMethod, replicaSpeed, facts, platform, language, mainRatio, detailRatio, ratio, selectedReferences, analysisRequestId, url, sourcePlatform, imageTab, selected, productDocId: productDoc?.id, sessionId: sessionDoc?.id })); }, [workspaceId, hydratedWorkspace, products, references, mode, replicationMethod, replicaSpeed, facts, platform, language, mainRatio, detailRatio, ratio, selectedReferences, analysisRequestId, url, sourcePlatform, imageTab, selected, productDoc?.id, sessionDoc?.id]);
  useEffect(() => {
    if (!workspaceId || !pendingId) return;
    const generation = draftGeneration.current;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await replicaRequest<ReplicaDocument>(workspaceId, `/documents/${pendingId}`, undefined, controller.signal);
        if (controller.signal.aborted || generation !== draftGeneration.current) return;
        if (value.kind === "product") setProductDoc(current => mergeReplicaProgress(current, value));
        else setSessionDoc(current => mergeReplicaProgress(current, value));
        if (value.status === "failed") setError(value.payload.error ?? "任务暂未完成。");
      } catch (caught) { if (!controller.signal.aborted && generation === draftGeneration.current) setError(caught instanceof Error ? caught.message : "状态读取暂未完成。"); }
      if (!controller.signal.aborted && generation === draftGeneration.current) timer = setTimeout(() => void poll(), 2000);
    };
    timer = setTimeout(() => void poll(), 1000);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [workspaceId, pendingId, pendingKind]);
  useEffect(() => { if (!preview) return; const close = (event: KeyboardEvent) => { if (event.key === "Escape") setPreview(""); }; window.addEventListener("keydown", close); return () => window.removeEventListener("keydown", close); }, [preview]);

  useEffect(() => {
    if (!workspaceId || hydratedWorkspace !== workspaceId) return;
    const controller = new AbortController();
    const load = () => {
      const generation = draftGeneration.current;
      const params = new URLSearchParams(window.location.search);
      const id = params.get("sessionId"), jobId = params.get("jobId");
      const path = id ? `/documents/${encodeURIComponent(id)}` : jobId ? `/jobs/${encodeURIComponent(jobId)}/session` : null;
      if (path) void replicaRequest<ReplicaDocument>(workspaceId, path, undefined, controller.signal)
        .then(item => { if (!controller.signal.aborted && generation === draftGeneration.current && item.kind === "session") {
          setSessionDoc(item); setReplicationMethod(item.payload.replication_method ?? "exact"); setReplicaSpeed(item.payload.replica_speed === "fast" ? "fast" : "standard"); setInstructions(item.payload.confirmed_instructions ?? []);
          setProducts((item.payload.product_asset_ids ?? []).map((assetId, index) => ({id: assetId, name: `产品图 ${index + 1}`})));
          const ids = [...new Set((item.payload.references ?? []).map(value => value.asset_id))];
          setReferences(ids.map((assetId, index) => ({id: assetId, name: `参考图 ${index + 1}`}))); setSelectedReferences(ids);
          setMode("image"); setFacts(item.payload.facts ?? ""); setPlatform(item.payload.platform_code ?? "auto"); setLanguage(item.payload.language ?? "zh-CN"); setProductDoc(undefined); setSelected([]);
        } })
        .catch(caught => { if (!controller.signal.aborted && generation === draftGeneration.current) setError(caught.message); });
    };
    load(); window.addEventListener("popstate", load);
    return () => { controller.abort(); window.removeEventListener("popstate", load); };
  }, [workspaceId, hydratedWorkspace]);
  const currentSessionId = sessionDoc?.id;
  useEffect(() => { if (currentSessionId) window.history.replaceState(null, "", `/style-replica?sessionId=${encodeURIComponent(currentSessionId)}`); }, [currentSessionId]);

  const perform = async (label: string, action: (isCurrent: () => boolean) => Promise<void>) => {
    if (actionLock.current) return;
    const generation = draftGeneration.current;
    const isCurrent = () => generation === draftGeneration.current;
    actionLock.current = true; setBusy(label); setError("");
    try { await action(isCurrent); }
    catch (caught) { if (isCurrent()) setError(caught instanceof Error ? caught.message : "处理失败，请稍后再试。"); }
    finally { if (isCurrent()) { actionLock.current = false; setBusy(""); } }
  };
  const uploadFiles = async (files: FileList | File[] | null, role: "product" | "reference") => {
    if (!files?.length || !workspaceId || working || sessionDoc || uploadBatchLock.current) return;
    const current = role === "product" ? products : references;
    const limit = role === "product" ? 6 : 20;
    const selected = validateImageUpload(Array.from(files), { multiple: true, maxFiles: Math.max(0, limit - current.length) });
    if (selected.error) { setError(selected.error); return; }
    if (!selected.files.length) return;
    uploadBatchLock.current = true;
    const generation = draftGeneration.current;
    try {
      await perform("上传图片", async isCurrent => {
        const added: AssetChoice[] = [];
        const failures: string[] = [];
        for (const file of selected.files) {
          if (!isCurrent()) return;
          try {
            if (file.size > 20 * 1024 * 1024) throw new Error("单张图片不能超过20MB。");
            const result = await upload.mutateAsync({ file });
            if (!isCurrent()) return;
            added.push({ id: result.id, name: file.name });
            if (role === "product") setProducts([...current, ...added]);
            else { setReferences([...current, ...added]); setSelectedReferences(ids => [...new Set([...ids, result.id])]); }
          } catch (caught) { failures.push(caught instanceof Error ? caught.message : "上传失败"); }
        }
        if (failures.length) throw new Error(failures[0] + (added.length ? "，已上传的图片已保留。" : ""));
      });
    } finally { if (generation === draftGeneration.current) uploadBatchLock.current = false; }
  };
  const fetchProduct = () => workspaceId && perform("拉取商品", async isCurrent => { setSelected([]); setHiddenSources([]); setProductDoc(undefined); const value = await replicaRequest<ReplicaDocument>(workspaceId, "/products", { url, platform: sourcePlatform }); if (isCurrent()) setProductDoc(value); });
  const analyse = () => workspaceId && perform("准备分析", async isCurrent => {
    let choices: Plan["reference"][] = [];
    if (mode === "link") {
      if (!productDoc || !selected.length) throw new Error("请先拉取商品并勾选参考图。");
      const readable = await Promise.all(selected.map(src => probeReferenceImage(src)));
      if (!isCurrent()) return;
      const invalid = selected.filter((_, index) => readable[index] === "blank");
      if (invalid.length) { invalid.forEach(hideSource); throw new Error("已移除尺寸过小的参考图，请重新选择后开始生成。"); }
      const imported = await replicaRequest<{ asset_id: string; url: string; role: string }[]>(workspaceId, `/products/${productDoc.id}/import`, { urls: selected });
      if (!isCurrent()) return;
      choices = imported.map((item, index) => ({ asset_id: item.asset_id, title: `${item.role} ${index + 1}`, aspect_ratio: "follow-reference" }));
    } else {
      if (!selectedReferenceCount) throw new Error("请勾选要复刻的参考图。");
      choices = references.filter(item => selectedReferences.includes(item.id)).map((item, index) => ({ asset_id: item.id, title: `参考图 ${index + 1}`, aspect_ratio: "follow-reference" }));
    }
    if (!products.length) throw new Error("请上传自己的产品图。");
    setInstructions([]); setJobStatuses({});
    const result = await replicaRequest<ReplicaDocument>(workspaceId, "/sessions", { request_id: analysisRequestId || crypto.randomUUID(), product_asset_ids: products.map(item => item.id), references: choices, replication_method: replicationMethod, replica_speed: replicaSpeed, auto_start_after_plan: true, platform_code: platform === "auto" ? product?.targetPlatform ?? product?.platform ?? "tmall" : platform, language, facts });
    if (isCurrent()) setSessionDoc(result);
    // The session is durable before image jobs exist; update the global task
    // centre immediately rather than waiting for its next polling interval.
    void queryClient.invalidateQueries({ queryKey: ["replica-creation-records", workspaceId] });
  });
  const confirm = () => workspaceId && sessionDoc && perform("提交生成", async isCurrent => { try { const value = await replicaRequest<ReplicaDocument>(workspaceId, `/sessions/${sessionDoc.id}/confirm`, { plan_hash: sessionDoc.payload.plan_hash, instructions: sessionDoc.payload.confirmed_instructions ?? instructions }); if (isCurrent()) setSessionDoc(value); } catch (caught) { const value = await replicaRequest<ReplicaDocument>(workspaceId, `/documents/${sessionDoc.id}`); if (isCurrent()) setSessionDoc(value); throw caught; } });
  const retryImage = (jobId: string) => workspaceId && sessionDoc && perform("重新生成", async isCurrent => { const requestId = retryIntents.current[jobId] ?? crypto.randomUUID(); retryIntents.current[jobId] = requestId; const value = await replicaRequest<ReplicaDocument>(workspaceId, `/sessions/${sessionDoc.id}/jobs/${jobId}/retry`, { request_id: requestId }); if (isCurrent()) setSessionDoc(value); });
  const restoreSession = (item: ReplicaDocument) => { if (sessionDoc?.id !== item.id) draftGeneration.current += 1; actionLock.current = false; uploadBatchLock.current = false; setBusy(""); window.history.replaceState(null, "", `/style-replica?sessionId=${encodeURIComponent(item.id)}`); setSessionDoc(item); setReplicationMethod(item.payload.replication_method ?? "exact"); setReplicaSpeed(item.payload.replica_speed === "fast" ? "fast" : "standard"); setInstructions(item.payload.confirmed_instructions ?? []); setProducts((item.payload.product_asset_ids ?? []).map((id, index) => ({ id, name: `产品图 ${index + 1}` }))); const referenceIds = [...new Set((item.payload.references ?? []).map(value => value.asset_id))]; setReferences(referenceIds.map((id, index) => ({ id, name: `参考图 ${index + 1}` }))); setSelectedReferences(referenceIds); setMode("image"); setFacts(item.payload.facts ?? ""); setPlatform(item.payload.platform_code ?? "auto"); setLanguage(item.payload.language ?? "zh-CN"); setProductDoc(undefined); setSelected([]); setError(""); setModal(null); };
  const openHistorySession = (item: ReplicaDocument) => workspaceId && perform("读取任务", async isCurrent => { const value = await replicaRequest<ReplicaDocument>(workspaceId, `/documents/${item.id}`); if (isCurrent()) restoreSession(value); });
  const showHistory = () => workspaceId && perform("读取历史", async isCurrent => { const value = await replicaRequest<ReplicaDocument[]>(workspaceId, "/sessions"); if (isCurrent()) { setHistory(value); setModal("history"); } });
  const retryAnalysis = () => workspaceId && sessionDoc && perform("重试分析", async isCurrent => { const value = await replicaRequest<ReplicaDocument>(workspaceId, `/sessions/${sessionDoc.id}/retry`, {}); if (isCurrent()) setSessionDoc(value); });
  const reviseAnalysis = () => workspaceId && sessionDoc && perform("调整剩余方案", async isCurrent => { const value = await replicaRequest<ReplicaDocument>(workspaceId, `/sessions/${sessionDoc.id}/revise`, {}); if (isCurrent()) setSessionDoc(value); });
  const editSources = () => {
    draftGeneration.current += 1; actionLock.current = false; uploadBatchLock.current = false;
    setBusy(""); setProductDoc(current => current?.status === "fetching" ? undefined : current);
    window.history.replaceState(null, "", "/style-replica"); setAnalysisRequestId(crypto.randomUUID());
    setSessionDoc(undefined); setInstructions([]); setError(""); setJobStatuses({}); retryIntents.current = {};
  };
  const reset = () => {
    editSources();
    setProducts([]); setReferences([]); setSelectedReferences([]); setSelected([]);
    setProductDoc(undefined); setFacts(""); setUrl(""); setHiddenSources([]);
    setPreview(""); setModal(null); setHistory([]);
    setMode("link"); setReplicationMethod("exact"); setReplicaSpeed("standard"); setSourcePlatform("auto"); setImageTab("imageUrls");
    setPlatform("auto"); setLanguage("zh-CN"); setMainRatio("1:1"); setDetailRatio("3:4"); setRatio("follow-reference");
    upload.reset();
    if (workspaceId) localStorage.removeItem(`polaris-replica:${workspaceId}`);
  };
  const uploadBox = (role: "product" | "reference", items: AssetChoice[], limit: number) => <div className={styles.uploadArea}>
    {items.length > 0 && <div className={styles.uploadGrid}>{items.map(item => <div key={item.id} className={styles.uploadThumb}>{workspaceId && <AssetImage workspaceId={workspaceId} assetId={item.id} onClick={setPreview} />}<button type="button" disabled={working || !!sessionDoc} onClick={() => role === "product" ? setProducts(products.filter(p => p.id !== item.id)) : (setReferences(references.filter(p => p.id !== item.id)), setSelectedReferences(selectedReferences.filter(id => id !== item.id)))} aria-label={`移除${item.name}`}><X size={12} /></button></div>)}</div>}
    {items.length < limit && <ImageDropZone label={role === "product" ? "产品图" : "参考图"} multiple maxFiles={limit - items.length} disabled={working || !!sessionDoc} busy={upload.isPending} onFiles={files => uploadFiles(files, role)} showHint={false}>
      {({ openFileDialog, disabled }) => <button type="button" className={items.length ? styles.addUpload : styles.uploadTarget} onClick={openFileDialog} disabled={disabled}><Upload size={25} /><span>{items.length ? "添加图片" : role === "product" ? "拖入产品图，或点击上传" : "拖入参考图，或点击上传"}</span>{!items.length && <small>JPG / PNG / WebP · 单张不超过20MB</small>}</button>}
    </ImageDropZone>}

  </div>;

  return <div className={styles.page}>
    <div className={styles.stepper} aria-label="复刻进度">{STEPS.map((label, index) => <div key={label} className={`${styles.stepItem} ${index === step ? styles.stepCurrent : ""} ${index < step ? styles.stepDone : ""}`}><span>{index < step ? <Check size={13} /> : index + 1}</span><b>{index === 4 ? generationStepLabel : label}</b>{index < STEPS.length - 1 && <i />}</div>)}</div>
    <div className={styles.layout}>
      <aside className={styles.sidebar}>
        <button className={styles.feeLink} onClick={() => setModal("fees")}>用量说明 <ChevronRight size={12} /></button>
        <fieldset className={styles.inputFields} disabled={working || !!sessionDoc}>
        <div className={styles.methodChoice} role="radiogroup" aria-label="复刻方式">{([["exact", "一比一模仿", "风格最像，存在侵权风险"], ["style", "风格复刻", "提取风格机制，原创重构商品画面"]] as const).map(([value, title, note]) => <label key={value} className={replicationMethod === value ? styles.methodSelected : ""}><input type="radio" name="replication-method" value={value} checked={replicationMethod === value} disabled={working} onChange={() => selectReplicationMethod(value)} /><span><strong>{title}</strong><small>{note}</small></span></label>)}</div>
        <div className={styles.fieldHeading}><strong>生成版本</strong></div>
        <div className={styles.methodChoice} role="radiogroup" aria-label="生成版本">{([["fast", "快速版（约5分钟）"], ["standard", "精致版（约40分钟）"]] as const).map(([value, title]) => <label key={value} className={replicaSpeed === value ? styles.methodSelected : ""}><input type="radio" name="replica-speed" value={value} checked={replicaSpeed === value} disabled={working} onChange={() => selectReplicaSpeed(value)} /><span><strong>{title}{value === "fast" && <GenerationRecommendation />}</strong></span></label>)}</div><div className={styles.fieldHeading}><strong><ImagePlus size={15} /><em>*</em>上传你的产品图</strong><small>{products.length}/6</small></div>
        <p className={styles.hint}>上传必要角度，清晰展示商品外观；产品图决定最终商品身份。</p>
        {uploadBox("product", products, 6)}
        <div className={styles.modeTabs} role="tablist" aria-label="参考来源"><button role="tab" aria-selected={mode === "link"} className={mode === "link" ? styles.activeTab : ""} onClick={() => setMode("link")}>商品链接</button><button role="tab" aria-selected={mode === "image"} className={mode === "image" ? styles.activeTab : ""} onClick={() => setMode("image")}>风格参考图</button></div>
        {mode === "link" ? <>
          <label className={styles.field}><span><em>*</em>商品链接 / 商品 ID</span><div className={styles.linkInput}><input value={url} onChange={event => setUrl(event.target.value)} placeholder="粘贴商品链接或手淘分享文本" aria-label="商品链接" /><button disabled={working || !url.trim() || !workspaceId} onClick={() => void fetchProduct()}>{productDoc?.status === "fetching" ? <LoaderCircle className="spin" size={16} /> : "拉取商品"}</button></div></label>
          <label className={styles.field}><span>来源平台</span><ReplicaSelect label="来源平台" value={sourcePlatform} onChange={setSourcePlatform} disabled={working} options={[["auto", "从链接自动识别"], ...sourcePlatforms.map(item => [item.code, item.label] as const)]} /></label>
        </> : <>
          <div className={styles.fieldHeading}><strong><em>*</em>风格参考图</strong><small>{references.length}/20</small></div>{uploadBox("reference", references, 20)}
        </>}
        <div className={styles.outputGroup}><div className={styles.fieldHeading}><strong>发布到哪里</strong><small>平台与图片文案语言</small></div><div className={styles.dualFields}>
        <label className={styles.field}><span><em>*</em>目标平台</span><ReplicaSelect label="目标平台" value={platform} onChange={setPlatform} disabled={working || !!sessionDoc} options={[["auto", "智能匹配"], ["tmall", "天猫"], ["taobao", "淘宝"], ["jd", "京东"], ["pinduoduo", "拼多多"], ["douyin", "抖音"], ["1688", "1688"], ["amazon-us", "亚马逊"], ["temu", "TEMU"], ["shopee", "Shopee"], ["tiktok", "TikTok"]]} /></label>
        <label className={styles.field}><span><em>*</em>目标语言</span><ReplicaSelect label="目标语言" value={language} onChange={setLanguage} disabled={working || !!sessionDoc} options={[["zh-CN", "中文（简体）"], ["zh-TW", "中文（繁体）"], ["en-US", "英语"], ["ja-JP", "日语"], ["ko-KR", "韩语"], ["de-DE", "德语"], ["fr-FR", "法语"], ["es-ES", "西班牙语"], ["ar-SA", "阿拉伯语"], ["ru-RU", "俄语"]]} /></label>
        </div></div>
        <p className={styles.hint}>{replicaSpeed === "fast" ? "快速完成整套画面，参考长图会拆成独立图片。" : replicationMethod === "exact" ? "按参考图逐屏保留构图和比例，长图会先拆屏。" : "根据参考内容选择画幅；连续详情按相邻双屏生成。"}后台按所选参考图完成拆屏和画幅校验。</p>
        </fieldset><FormDisclosure title="补充商品信息与要求" summary={facts.trim() ? "已添加商品信息" : "选填 · 品牌、卖点与希望保留的构图"}><label className={styles.field}><span>商品卖点与复刻要求</span><textarea disabled={working || !!sessionDoc} value={facts} onChange={event => setFacts(event.target.value)} placeholder="写下自己的品牌、卖点、参数，或希望保留的构图。留空也可以开始。" maxLength={4000} rows={3} /></label></FormDisclosure>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <div className={styles.sidebarFooter}><button className={styles.startButton} disabled={!!sessionDoc || working || !workspaceId || !products.length || (mode === "image" ? !selectedReferenceCount : !selected.length)} onClick={() => void analyse()}>{working ? <><LoaderCircle className="spin" size={16} />{busy || (status === "analysing" ? "正在诊断" : "正在拉取商品")}</> : <><Sparkles size={16} />生成同行套图</>}</button>
        <p className={styles.footerNote}>{mode === "image" ? `已选择 ${selectedReferenceCount} 张参考图` : `已选择 ${selected.length} 张参考图`} · 选哪张，复刻哪张</p></div>
      </aside>
      <section className={styles.stage}>
        <header className={styles.stageHeader}><div><Sparkles size={18} /><h1>一键复刻</h1><span>让好灵感，成为你的商品图</span></div><nav><button className={styles.newReplicaButton} disabled={!!busy} onClick={reset} aria-label="新建复刻任务" title="当前任务会保留在历史记录中，继续在后台处理"><Plus size={18} />新建复刻</button><button onClick={() => void showHistory()}><History size={14} />历史记录</button><button onClick={() => setModal("help")}><CircleHelp size={14} />使用教程</button></nav></header>
        {sessionDoc && !native && <p className={styles.hint}>历史任务沿用此前的复刻方式；如需使用新方式，请重新诊断。</p>}
        {executionProgress && <section className={styles.executionProgress} aria-label="整组复刻进度" role="status"><strong>{executionHeading}</strong><progress max={100} value={overallProgress} /><span>{directDelivery ? `已生成 ${executionProgress.generated ?? 0} / ${executionProgress.total} 屏` : <>已审 {executionProgress.reviewed} / {executionProgress.total} 屏 · 通过 {executionProgress.accepted} 屏{executionProgress.rejected > 0 ? ` · 待修正 ${executionProgress.rejected} 屏` : ""}</>}</span><small>{directDelivery ? "按已确认方案生成，未进行自动审图。可从历史查看和下载。" : "整组处理后交付，可离开页面后从历史继续查看。"}</small></section>}
        {mode === "link" && product && !sessionDoc && <div className={styles.sourcePanel}><div className={styles.sourceHeading}><div><span className={styles.sourceBadge}>{sourcePlatforms.find(item => item.code === product.platform)?.label ?? product.platform}</span><strong>{product.title ?? "商品参考图"}</strong></div><span>已选 {selected.length}/20</span></div><div className={styles.selectionActions}><span>点击图片选择，长图会拆屏，最终数量以诊断方案为准</span><button onClick={() => setSelected([...new Set([...selected, ...sourceImages(activeImageTab)])].slice(0,20))}>全选本组</button><button onClick={() => setSelected([])}>清空选择</button></div><div className={styles.sourceTabs}>{[["imageUrls", "主图"], ["descImageUrls", "详情图"], ["skuImageUrls", "SKU 图"]].filter(([key]) => sourceImages(key as typeof imageTab).length > 0).map(([key, label]) => <button className={activeImageTab === key ? styles.sourceTabActive : ""} key={key} onClick={() => setImageTab(key as typeof imageTab)}>{label}<b>{sourceImages(key as "imageUrls" | "descImageUrls" | "skuImageUrls").length}</b></button>)}</div><div className={styles.sourceGrid}>{sourceImages(activeImageTab).map((src, index) => <div key={src} className={`${styles.sourceTile} ${selected.includes(src) ? styles.sourceSelected : ""}`}><img src={sourcePreview(src)} alt={`参考图 ${index + 1}`} loading="lazy" referrerPolicy="no-referrer" onLoad={event => { if (event.currentTarget.naturalWidth < 32 || event.currentTarget.naturalHeight < 32) hideSource(src); }} onError={() => hideSource(src)} onClick={() => setSelected(selected.includes(src) ? selected.filter(item => item !== src) : selected.length < 20 ? [...selected, src] : selected)} /><button aria-label={`${selected.includes(src) ? "取消选择" : "选择"}参考图 ${index + 1}`} onClick={() => setSelected(selected.includes(src) ? selected.filter(item => item !== src) : selected.length < 20 ? [...selected, src] : selected)}>{selected.includes(src) ? <Check size={13} /> : index + 1}</button><button className={styles.previewTile} onClick={() => setPreview(sourcePreview(src))}>放大</button></div>)}</div></div>}
        {mode === "image" && references.length > 0 && !sessionDoc && workspaceId && <div className={styles.sourcePanel}><div className={styles.sourceHeading}><strong>选择想要复刻的参考图</strong><span>已选 {selectedReferenceCount}/{references.length}</span></div><div className={styles.selectionActions}><span>全部选中参考均纳入分析，长图会拆屏</span><button onClick={() => setSelectedReferences(references.map(item => item.id))}>全选</button><button onClick={() => setSelectedReferences([])}>清空选择</button></div><div className={styles.sourceGrid}>{references.map((item, index) => <div key={item.id} className={`${styles.sourceTile} ${selectedReferences.includes(item.id) ? styles.sourceSelected : ""}`}><AssetImage workspaceId={workspaceId} assetId={item.id} onClick={() => setSelectedReferences(selectedReferences.includes(item.id) ? selectedReferences.filter(id => id !== item.id) : [...selectedReferences, item.id])} /><button aria-label={`${selectedReferences.includes(item.id) ? "取消选择" : "选择"}参考图 ${index + 1}`} onClick={() => setSelectedReferences(selectedReferences.includes(item.id) ? selectedReferences.filter(id => id !== item.id) : [...selectedReferences, item.id])}>{selectedReferences.includes(item.id) ? <Check size={13} /> : index + 1}</button></div>)}</div></div>}
        {status === "analysing" && <div className={styles.analysisState}><div className={styles.orbit}><Sparkles size={28} /></div><h2>正在准备你的整套画面</h2><p>保留参考顺序，逐屏规划商品表达。可离开页面，稍后从历史继续查看。</p><progress max={100} value={overallProgress} /><small>{native ? replicaAnalysisMessage(sessionDoc!) ?? sessionDoc?.payload.analysis_stage ?? "正在准备复刻方案" : `${plans.length} / ${sessionDoc?.payload.references?.length ?? 0} 张方案已完成`}</small></div>}
        {status === "failed" && !jobs.length && <div className={styles.emptyState}><CircleHelp size={38} /><h2>{planFailure?.title ?? segmentationFailure?.title ?? "本次处理暂未完成"}</h2><p>{planFailure?.message ?? segmentationFailure?.message ?? sessionDoc?.payload.error}</p>{planFailure && sessionDoc?.payload.analysis_revision_available && <button disabled={!!busy} onClick={() => void reviseAnalysis()}>{sessionDoc?.payload.replication_method === "style" ? "保留分析，修正风格方案" : "保留已通过方案，调整剩余屏"}</button>}{!planFailure && <>{sessionDoc?.payload.analysis_resume_available && <small>已完成的步骤已保留，继续时会复用仍然有效的方案与校验结果。</small>}{(!segmentationFailure || sessionDoc?.payload.segmentation_repair_available || sessionDoc?.payload.segmentation_restart_available) && <button disabled={!!busy} onClick={() => void retryAnalysis()}>{sessionDoc?.payload.segmentation_repair_available ? "保留素材，校正拆屏" : sessionDoc?.payload.segmentation_restart_available ? "重新诊断参考图" : sessionDoc?.payload.analysis_resume_available ? "继续处理剩余步骤" : "重试未完成的分析"}</button>}</>}<button onClick={editSources}>{planFailure && sessionDoc?.payload.replication_method === "style" && !sessionDoc?.payload.analysis_revision_available ? "保留素材，重新诊断" : "返回修改选图"}</button></div>}
        {status === "awaiting_confirmation" && !sessionDoc?.payload.auto_start_after_plan && <div className={styles.planPanel}><div className={styles.planIntro}><div><span className={styles.eyebrow}>你的专属复刻方案</span><h2>灵感已就位，确认后开始绘制</h2><p>你的产品图决定真实外观。确认拆屏与方案后开始生成；补充要求请返回修改并重新诊断。</p></div><button className={styles.textButton} onClick={editSources}><RotateCcw size={14} />返回修改</button></div><div className={styles.plans}>{plans.map((plan, index) => <article className={styles.planCard} key={index}><div className={styles.planReference}>{workspaceId && <AssetImage workspaceId={workspaceId} assetId={plan.reference.asset_id} onClick={setPreview} />}</div><div><span className={styles.eyebrow}>方案 {String(index + 1).padStart(2, "0")}</span><h3>{plan.reference.title}</h3><p>{analysisText(plan.analysis.commercial_intent) || "保留参考构图、光影与色调，用你的商品重新演绎。"}</p><details><summary>查看画面方案</summary><p>{plan.prompt}</p></details>{!native && <textarea rows={2} disabled={sessionDoc?.payload.confirmed_instructions !== undefined} value={sessionDoc?.payload.confirmed_instructions?.[index] ?? instructions[index] ?? ""} maxLength={4000} onChange={event => setInstructions(current => plans.map((_, position) => position === index ? event.target.value : current[position] ?? ""))} placeholder="补充本张图的要求（选填）" aria-label={`方案${index + 1}补充要求`} />}</div></article>)}</div><div className={styles.confirmBar}><div><strong>共 {plans.length} 张独立图片</strong><CreditCostHint jobType="reference_remix" count={plans.length} /><small>生成结果自动进入个人素材库与生成记录</small></div><button disabled={!!busy} className={styles.startButton} onClick={() => void confirm()}>{busy ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}确认方案并生成</button></div></div>}
        {status === "awaiting_confirmation" && sessionDoc?.payload.auto_start_after_plan && <div className={styles.emptyState}><LoaderCircle className={sessionDoc.payload.automatic_submission_error ? "" : "spin"} size={28} /><h2>{sessionDoc.payload.automatic_submission_error ? "生成任务暂未提交成功" : "正在准备生成套图"}</h2><p>{sessionDoc.payload.automatic_submission_error || "图片方案校验完成后，将自动开始绘制。"}</p>{sessionDoc.payload.automatic_submission_error && <button disabled={!!busy} onClick={() => void confirm()}>重试提交生成</button>}</div>}
        {jobs.length > 0 && workspaceId && <div className={styles.results}><div className={styles.planIntro}><div><span className={styles.eyebrow}>生成结果</span><h2>{sessionDoc?.payload.submission_complete === false ? `还有 ${plans.length - jobs.length} 张待生成` : complete ? (jobs.every(id => jobStatuses[id] === "completed") ? "你的商品图已完成" : executionResume ? "处理已中断，可继续剩余步骤" : "部分图片暂未通过验收，可重试") : "灵感正在成为新画面"}</h2><p>点击图片放大，下载保存，或进入任务继续编辑。</p></div><div>{sessionDoc?.payload.submission_complete === false && <button disabled={!!busy} className={styles.textButton} onClick={() => void confirm()}>继续提交剩余图片</button>}<button className={styles.textButton} onClick={reset}><Plus size={14} />新建复刻</button></div></div><div className={styles.resultGrid}>{(native ? plans.map((_, index) => ({ id: jobs[0], index })) : jobs.map((id, index) => ({ id, index }))).map(({id, index}) => <ResultCard key={`${id}-${index}`} workspaceId={workspaceId} jobId={id} sessionId={sessionDoc!.id} outputIndex={native ? index : 0} retryWhole={native} fastSuite={sessionDoc?.payload.replica_speed === "fast"} resumeWhole={executionResume} moduleProgress={native ? executionProgress?.modules[index] : undefined} title={plans[index]?.reference.title ?? `商品图 ${index + 1}`} onPreview={setPreview} onStatus={reportStatus} onRetry={id => void retryImage(id)} retrying={busy === "重新生成"} />)}</div></div>}
        {!sessionDoc && (mode === "link" ? !product : !references.length) && <div className={styles.emptyState}><WorkspaceVisualPreview /><h2>好构图，换成你的好商品</h2><p>上传产品图，添加商品链接或风格参考图<br />点击「生成同行套图」，自动完成规划与创作</p><div className={styles.emptySteps}><span>01 上传产品</span><ChevronRight size={13} /><span>02 选择参考</span><ChevronRight size={13} /><span>03 查看成图</span></div></div>}
      </section>
    </div>
    {modal && <div className={styles.overlay} onClick={() => setModal(null)}><section className={styles.modal} role="dialog" aria-modal="true" aria-label={modal === "history" ? "复刻历史" : modal === "fees" ? "用量说明" : "使用教程"} onClick={event => event.stopPropagation()}><button className={styles.closeModal} onClick={() => setModal(null)} aria-label="关闭"><X size={20} /></button>{modal === "history" ? <><h2>复刻历史</h2>{history.length ? history.map(item => <button className={styles.historyRow} key={item.id} onClick={() => void openHistorySession(item)}><span>{formatBeijingDate(item.created_at, { dateStyle: "short", timeStyle: "medium" })}<small>{item.payload.replica_speed === "fast" ? "快速版" : "精致版"} · {item.payload.plans?.length ?? item.payload.references?.length ?? 0} 张图片</small></span><b>{item.status === "awaiting_confirmation" ? "待确认" : item.status === "analysing" ? "诊断中" : item.status === "failed" ? "未完成" : "查看结果"}</b><ChevronRight size={16} /></button>) : <p>还没有复刻记录，提交后即可在这里查看。</p>}</> : modal === "fees" ? <><h2>用量说明</h2><p>商品拉取通过第三方数据服务完成；同一商品当天再次查询会复用已有结果。</p><p>一键复刻按成功生成图片每张 15 积分结算；提交时预留，失败或未执行部分释放。重新生成会创建新的计费任务。</p><Link href="/dashboard">查看创作记录与进度 <ChevronRight size={14} /></Link></> : <><h2>把参考风格，变成你的商品图</h2><ol><li>上传自己的产品图，让外观、材质与品牌清晰可见。</li><li>粘贴商品链接选图，或直接上传风格参考图。</li><li>选择目标平台与语言，可补充自己的真实卖点。</li><li>提交素材后自动分析、规划与生成。</li><li>查看成图，结果保存在素材库和生成记录中。</li></ol><p>参考图用于学习构图、光影和排版。商品参数、认证与售后承诺请以自己的真实信息为准。</p></>}</section></div>}
    {preview && <div className={styles.lightbox} role="dialog" aria-modal="true" aria-label="图片预览" onClick={() => setPreview("")}><button onClick={() => setPreview("")} aria-label="关闭图片预览"><X size={24} /></button><img src={preview} alt="图片预览" referrerPolicy="no-referrer" /></div>}
  </div>;
}
