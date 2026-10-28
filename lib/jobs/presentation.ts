import type { Job, JobDetail, JobItemStatus, JobStatus } from "@/lib/api/types";
import { formatBeijingDate } from "../datetime.mjs";

const JOB_TYPE_LABELS: Record<Job["type"], string> = {
  product_replica: "旧版商品复刻",
  local_edit: "一句话改图",
  product_image: "万能生图",
  reference_remix: "一键模仿",
  model_pose_transfer: "模特换姿势",
  creative_image: "电商创意图",
  detail_pack: "商品详情图",
  domestic_detail_review: "详情整页审核",
  amazon_visual_suite: "亚马逊套图",
  domestic_main_image: "国内电商主图",
};

const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  draft: "草稿",
  queued: "排队中",
  planning: "规划中",
  awaiting_confirmation: "待确认",
  processing: "处理中",
  awaiting_final_review: "等待整组确认",
  cancelling: "取消中",
  partial: "部分成功",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const JOB_STAGE_LABELS: Record<string, string> = {
  draft: "草稿",
  queued: "排队中",
  planning: "规划中",
  awaiting_confirmation: "等待确认",
  processing: "处理中",
  generating: "生成中",
  awaiting_final_review: "等待整组确认",
  validating: "校验中",
  validation: "校验中",
  organizing: "整理结果中",
  planning_failed: "规划失败",
  intake_confirmation_required: "等待商品事实确认",
  analyzing_confirmed_sources: "分析已确认商品信息",
  suite_quality_reported: "质量检查已完成",
  suite_quality_passed: "质量检查通过",
  suite_review_reported: "套图审核已完成",
  partial: "部分成功",
  completed: "已完成",
  failed: "失败",
  cancelling: "取消中",
  cancelled: "已取消",
};

const JOB_ITEM_STATUS_LABELS: Record<JobItemStatus, string> = {
  pending: "待处理",
  queued: "排队中",
  running: "执行中",
  retry_wait: "等待重试",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
  skipped: "已跳过",
  reused: "已复用",
};

const JOB_ASSET_ROLE_LABELS: Record<string, string> = {
  source: "源图",
  product: "商品图",
  product_source: "商品源图",
  model_source: "模特源图",
  pose_reference: "姿势参考",
  reference: "参考图",
  competitor_reference: "竞品参考",
  input: "输入素材",
  output: "输出结果",
  deliverable: "可交付结果",
  selection_mask: "授权选区",
  product_reference: "商品素材",
  product_identity_authority: "商品身份素材",
  white_background_product: "白底商品图",
  product_detail: "商品细节图",
  style_reference: "风格参考",
  package_reference: "包装参考",
  scene_reference: "场景参考",
  brand_reference: "品牌参考",
  local_edit_result: "局部编辑结果",
  product_image_result: "商品图结果",
  reference_remix_result: "参考图重绘结果",
  model_pose_transfer_result: "模特姿势迁移结果",
  creative_image_result: "创意图结果",
  detail_page: "详情页结果",
  amazon_visual: "Amazon 视觉结果",
  domestic_main_image: "国内电商主图结果",
  provider_raw: "生成中间产物",
};

export function jobTypeLabel(type: Job["type"]): string { return JOB_TYPE_LABELS[type]; }
export function jobStatusLabel(status: JobStatus): string { return JOB_STATUS_LABELS[status]; }

/** Uses backend stage values as the source of truth; unknown values remain visible. */
export function jobStageLabel(stage: string | null | undefined, status: JobStatus): string {
  if (stage && JOB_STAGE_LABELS[stage]) return JOB_STAGE_LABELS[stage];
  if (stage) return `阶段：${stage}`;
  return JOB_STATUS_LABELS[status];
}

export function jobItemStatusLabel(status: JobItemStatus): string { return JOB_ITEM_STATUS_LABELS[status]; }
export function jobAssetRoleLabel(role: string): string { return JOB_ASSET_ROLE_LABELS[role] ?? role.replaceAll("_", " "); }
export function jobTitle(job: Job | JobDetail): string { return job.display_name?.trim() || `${jobTypeLabel(job.type)} / ${job.id.slice(0, 8)}`; }
export function jobCount(job: Job | JobDetail): string { return `${job.succeeded_item_count} / ${job.expected_item_count} 项`; }

export function jobNote(job: Job | JobDetail): string {
  if (job.type === "detail_pack" && job.stage === "intake_confirmation_required") return "请先确认商品事实，随后审阅完整详情规划。";
  if (job.status === "failed" && "items" in job) {
    const failed = job.items.find((item) => item.status === "failed");
    if (failed?.error_code) return safeJobFailureSummary(failed.error_code, failed.error_details);
  }
  if (job.status === "partial") return "部分结果已完成，可在详情中查看并重试失败项目。";
  if (job.status === "failed") return "生成未完成，请查看详情中的安全错误摘要。";
  if (job.status === "awaiting_confirmation") return "生成方案已经就绪，等待确认后继续。";
  if (job.status === "cancelling") return "正在安全取消任务。";
  if (job.status === "cancelled") return "任务已取消，不会继续生成。";
  if (job.status === "completed") {
    return job.output_asset_count > 0
      ? `任务已完成，共 ${job.output_asset_count} 个输出结果。`
      : "任务已完成，但没有可交付输出。";
  }
  return job.stage ? `当前阶段：${jobStageLabel(job.stage, job.status)}` : "任务正在处理中。";
}

export function safeJobFailureSummary(errorCode?: string | null, errorDetails?: unknown): string {
  if (errorCode === "PROVIDER_OUTCOME_UNKNOWN") {
    return "生成请求的结果尚未确认，系统已停止自动重试，请联系管理员核查，避免重复生成。";
  }
  const details = errorDetails && typeof errorDetails === "object" ? errorDetails as Record<string, unknown> : {};
  const providerError = details.provider_error && typeof details.provider_error === "object" ? details.provider_error as Record<string, unknown> : {};
  if (errorCode === "IMAGE_CONTENT_REJECTED" || ["content_policy_violation", "content_policy_rejected", "safety_violation", "moderation_blocked"].includes(String(providerError.code))) {
    return "生成服务因内容限制拒绝了本次请求，未返回图片。原素材和要求已保留。";
  }
  if (errorCode?.startsWith("LOCAL_EDIT_VISUAL_REVIEW") && errorCode !== "LOCAL_EDIT_VISUAL_REVIEW_REJECTED") {
    return "图片已生成，但审核服务暂时不可用。重试会继续审核已保存的图片。";
  }
  if (errorCode === "LOCAL_EDIT_VISUAL_REVIEW_REJECTED") {
    return "候选图未通过视觉审核，请调整修改要求或选区后重试。";
  }
  if (errorCode?.startsWith("LOCAL_EDIT_RAW_")) {
    return "已保存的候选图未通过完整性校验，请重新提交改图任务。";
  }
  if (errorCode === "IMAGE_REQUEST_FAILED") {
    return "生成服务暂时不可用，请稍后重试。";
  }
  if (errorCode === "CREATIVE_VISUAL_ANALYSIS_INVALID" || errorCode === "PLANNER_SCHEMA_INVALID") {
    return "规划服务返回的数据不完整，请稍后重试。";
  }
  if (errorCode === "AURORA_SEQUENCE_HALTED") {
    return "前序图片未完成，后续图片已停止生成。";
  }
  if (errorCode?.includes("TIMEOUT")) {
    return "生成服务响应超时，请稍后重试。";
  }
  if (errorCode?.includes("QUOTA") || errorCode?.includes("LIMIT")) {
    return "当前生成额度或服务限额不足，请稍后重试或联系管理员。";
  }
  return "该项目未能生成可交付结果，请按页面提供的操作重试。";
}

export function formatDate(value: string): string {
  return formatBeijingDate(value, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "-";
  return formatBeijingDate(value, { year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
