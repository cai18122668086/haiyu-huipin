import type { components } from "./generated/openapi";

export type Workspace = components["schemas"]["WorkspaceResponse"];
export type Asset = components["schemas"]["AssetResponse"];
export type AssetList = components["schemas"]["AssetListResponse"];
export type AssetOriginKind = components["schemas"]["AssetOriginKind"];
export type Job = components["schemas"]["JobListItemResponse"];
export type JobDetail = components["schemas"]["JobDetailResponse"];
export type DomesticDetailFinalReview = components["schemas"]["DomesticDetailFinalReviewResponse"];
export type JobList = components["schemas"]["JobListResponse"];
export type JobStatus = components["schemas"]["JobStatus"];
export type JobItemStatus = components["schemas"]["JobItemStatus"];
export type JobType = components["schemas"]["JobType"];
export type RetryScope = components["schemas"]["RetryScope"];
export type LocalEditJobCreate = components["schemas"]["LocalEditJobCreate"];
export type ProductImageJobCreate = components["schemas"]["ProductImageJobCreate"];
export type ReferenceRemixJobCreate = components["schemas"]["ReferenceRemixJobCreate"];
export type ModelPoseTransferJobCreate =
  components["schemas"]["ModelPoseTransferJobCreate"];
export type CreativeImageJobCreate = components["schemas"]["CreativeImageJobCreate"];
export type DetailPackJobCreate = components["schemas"]["DetailPackJobCreate"];
export type AmazonVisualSuiteJobCreate =
  components["schemas"]["AmazonVisualSuiteJobCreate"];
export type DomesticMainImageJobCreate =
  components["schemas"]["DomesticMainImageJobCreate"];
export type JobCreate =
  | LocalEditJobCreate
  | ProductImageJobCreate
  | ReferenceRemixJobCreate
  | ModelPoseTransferJobCreate
  | CreativeImageJobCreate
  | DetailPackJobCreate
  | AmazonVisualSuiteJobCreate
  | DomesticMainImageJobCreate;
export type RuntimeStatus = components["schemas"]["RuntimeStatusResponse"];
export type CapabilityStatus = components["schemas"]["CapabilityStatus"];
export type DevelopmentProviderSettings =
  components["schemas"]["DevelopmentProviderSettingsResponse"];
export type DevelopmentProviderSettingsUpdate =
  components["schemas"]["DevelopmentProviderSettingsUpdate"];

export const activeJobStatuses: JobStatus[] = [
  "queued",
  "planning",
  "awaiting_confirmation",
  "processing",
  "cancelling",
];

export const terminalJobStatuses: JobStatus[] = [
  "partial",
  "completed",
  "failed",
  "cancelled",
];

export function isActiveJob(status: JobStatus): boolean {
  return activeJobStatuses.includes(status);
}
