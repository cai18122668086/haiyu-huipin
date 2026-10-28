import type { JobDetail } from "@/lib/api/types";

export type JobResultAsset = JobDetail["assets"][number];

const REFERENCE_ASSET_ROLES = new Set([
  "brand_reference",
  "competitor_reference",
  "package_reference",
  "pose_reference",
  "scene_reference",
  "style_reference",
]);

export function selectJobResultAssets(
  job: Pick<JobDetail, "assets"> | undefined,
): JobResultAsset[] {
  if (!job) return [];
  return job.assets
    .filter(
      (asset) =>
        asset.direction === "output" &&
        asset.is_deliverable &&
        Boolean(asset.asset_id),
    )
    .sort((left, right) => left.ordinal - right.ordinal);
}

export function selectJobInputAssets(
  job: Pick<JobDetail, "assets"> | undefined,
): JobResultAsset[] {
  if (!job) return [];
  return job.assets
    .filter(
      (asset) =>
        asset.direction === "input" && !REFERENCE_ASSET_ROLES.has(asset.role),
    )
    .sort((left, right) => left.ordinal - right.ordinal);
}

export function selectJobReferenceAssets(
  job: Pick<JobDetail, "assets"> | undefined,
): JobResultAsset[] {
  if (!job) return [];
  return job.assets
    .filter(
      (asset) =>
        asset.direction === "input" && REFERENCE_ASSET_ROLES.has(asset.role),
    )
    .sort((left, right) => left.ordinal - right.ordinal);
}

export function selectJobIntermediateAssets(
  job: Pick<JobDetail, "assets"> | undefined,
): JobResultAsset[] {
  if (!job) return [];
  return job.assets
    .filter(
      (asset) =>
        asset.direction === "intermediate" ||
        (asset.direction === "output" && !asset.is_deliverable),
    )
    .sort((left, right) => left.ordinal - right.ordinal);
}

export function canRetryJob(
  job: Pick<JobDetail, "allowed_actions"> | undefined,
): boolean {
  return Boolean(job?.allowed_actions.includes("retry"));
}

export function canDownloadJobArchive(job: JobDetail): boolean {
  if (
    !["detail_pack", "amazon_visual_suite", "domestic_main_image"].includes(job.type) ||
    job.expected_item_count < 1 ||
    selectJobResultAssets(job).length !== job.expected_item_count
  ) return false;
  if (job.status === "completed") return true;
  const preservedFailure = (item: JobDetail["items"][number]) =>
    item.status === "failed" &&
    item.error_details?.failure_kind === "review_infrastructure_failure" &&
    item.error_details.image_quality_failed === false &&
    item.error_details.generated_asset_preserved === true;
  return job.type === "amazon_visual_suite" && job.status === "partial" &&
    job.items.length === job.expected_item_count &&
    job.succeeded_item_count + job.failed_item_count === job.expected_item_count &&
    job.failed_item_count > 0 &&
    job.items.filter(preservedFailure).length === job.failed_item_count &&
    job.items.every(item => ["succeeded", "reused"].includes(item.status) || preservedFailure(item));
}
