import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { getFeatureById, getFeatureByRoute } from "../lib/feature-registry.ts";

const root = process.cwd();
const utf8 = (file) => readFile(path.join(root, file), "utf8");
const componentFile = "components/model-pose-transfer-workspace.tsx";
const cssFile = "components/model-pose-transfer-workspace.module.css";
const pageFile = "app/workspace/model-pose-transfer/page.tsx";
const occurrences = (source, needle) => source.split(needle).length - 1;

test("model pose transfer is an independent registered workspace", async () => {
  const page = await utf8(pageFile);
  const feature = getFeatureById("model-pose-transfer");

  assert.equal(feature?.route, "/workspace/model-pose-transfer");
  assert.equal(feature?.jobType, "model_pose_transfer");
  assert.equal(feature?.runtimeCapability, "model_pose_transfer");
  assert.equal(feature?.skillId, "ecommerce-model-pose-transfer");
  assert.equal(getFeatureByRoute("/workspace/model-pose-transfer")?.id, feature?.id);
  assert.equal(page.includes("ModelPoseTransferWorkspace"), true);
});

test("embedded browser catalog has stable slugs and original hashes for all 60 poses", async () => {
  const source = await utf8(componentFile);
  const catalogLines = source.split("\n").filter((line) => line.includes('{ slug: "'));
  const entries = catalogLines.map((line) => ({
    slug: line.split('slug: "')[1].split('"')[0],
    gender: line.split('genderGroup: "')[1].split('"')[0],
    sourceNumber: Number(line.split("sourceNumber: ")[1].split(",")[0]),
    hash: line.split('referenceSha256: "')[1].split('"')[0],
  }));

  assert.equal(entries.length, 60);
  assert.equal(entries.filter((entry) => entry.gender === "male").length, 21);
  assert.equal(entries.filter((entry) => entry.gender === "female").length, 39);
  assert.equal(new Set(entries.map((entry) => entry.slug)).size, 60);
  assert.equal(entries.some((entry) => entry.slug === "female-034"), false);
  assert.equal(entries.some((entry) => entry.slug === "female-037"), false);
  for (const entry of entries) {
    assert.equal(entry.slug.endsWith(String(entry.sourceNumber).padStart(3, "0")), true);
    assert.equal(entry.hash.length, 64);
  }
});

test("pose browsing uses responsive thumbnails and a non-cropped 960 preview", async () => {
  const source = await utf8(componentFile);
  const css = await utf8(cssFile);
  const cardCss = css.slice(css.indexOf(".poseImageFrame img"), css.indexOf(".poseImageFrame i {"));
  const previewCss = css.slice(css.indexOf(".posePreview > img"), css.indexOf(".posePreview > div"));

  assert.equal(source.includes("thumbnailUrl(pose, 320)"), true);
  assert.equal(source.includes("thumbnailUrl(pose, 640)"), true);
  assert.equal(source.includes("320w,"), true);
  assert.equal(source.includes("640w"), true);
  assert.equal(source.includes('loading="lazy"'), true);
  assert.equal(source.includes('decoding="async"'), true);
  assert.equal(source.includes("-960.webp"), true);
  assert.equal(cardCss.includes("object-fit: contain;"), true);
  assert.equal(cardCss.includes("object-position: center;"), true);
  assert.equal(previewCss.includes("object-fit: contain;"), true);
  assert.equal(source.includes("<strong>{poseLabel(pose)}</strong>"), false);
  assert.equal(source.includes("\u9009\u62e9\u540e\u67e5\u770b 960px \u6e05\u6670\u9884\u89c8"), false);
  assert.equal(source.includes("\u9ad8\u6e05\u539f\u56fe\u53c2\u4e0e\u751f\u6210"), false);
  assert.equal(source.includes("className={styles.previewNotice}"), false);
  assert.equal(source.includes("\u5f53\u524d\u4ec5\u652f\u6301\u57fa\u7840\u6d41\u7a0b\uff0c\u9ad8\u6e05\u751f\u6210\u5165\u53e3\u6682\u672a\u5f00\u653e\u3002"), false);
});

test("built-in poses materialize through the fixed endpoint before the unified job API", async () => {
  const source = await utf8(componentFile);
  const materializeStart = source.indexOf("async function materializeCatalogPose");
  const materializeEnd = source.indexOf("function useMaterializeCatalogPose", materializeStart);
  const materialize = source.slice(materializeStart, materializeEnd);
  const bodyStart = source.indexOf("const body = {", materializeEnd);
  const bodyEnd = source.indexOf("const created = await createJob.mutateAsync(body)", bodyStart);
  const jobBody = source.slice(bodyStart, bodyEnd);

  assert.equal(materialize.includes("/api/v1/workspaces/{workspace_id}/model-pose-transfer/pose-assets"), true);
  assert.equal(materialize.includes('"Idempotency-Key": idempotencyKey'), true);
  assert.equal(materialize.includes("pose_slug: pose.slug"), true);
  assert.equal(materialize.includes("reference_sha256: pose.referenceSha256"), true);
  assert.equal(source.includes("IdempotencyIntentRegistry"), true);
  assert.equal(source.includes("useCreateJob(workspace?.id)"), true);
  assert.equal(jobBody.includes('type: "model_pose_transfer"'), true);
  assert.equal(jobBody.includes("model_source_asset_id: modelSource.id"), true);
  assert.equal(jobBody.includes("pose_reference_asset_id: poseReferenceAsset.id"), true);
  assert.equal(jobBody.includes("pose_slug: selectedPose.slug"), true);
  assert.equal(jobBody.includes("pose_reference_sha256: selectedPose.referenceSha256"), true);
  assert.equal(jobBody.includes('aspect_ratio: "follow-source"'), true);
  assert.equal(jobBody.includes("image_count: 1"), true);
  assert.equal(jobBody.includes("satisfies JobCreate"), true);
  assert.equal(source.includes("as unknown as JobCreate"), false);
  assert.equal(source.includes("as never"), false);
  for (const forbidden of ["thumbnailUrl", "previewUrl", "src320", "src640", "-960.webp"]) {
    assert.equal(jobBody.includes(forbidden), false);
  }
});

test("completed or failed tasks can replace the source image without refreshing the page", async () => {
  const source = await utf8(componentFile);
  const restartStart = source.indexOf("const canRestartTask = Boolean(");
  const restartEnd = source.indexOf("const selectedPoseReady", restartStart);
  const restartLogic = source.slice(restartStart, restartEnd);
  const sourceTitle = source.indexOf('title="模特服装原图"');
  const sourceControlStart = source.lastIndexOf("<UploadControl", sourceTitle);
  const sourceControlEnd = source.indexOf("/>", sourceTitle);
  const sourceControl = source.slice(sourceControlStart, sourceControlEnd);
  const resetStart = source.indexOf("const resetTask = () => {");
  const resetEnd = source.indexOf("const progressText", resetStart);
  const resetLogic = source.slice(resetStart, resetEnd);

  for (const status of ["partial", "completed", "failed", "cancelled"]) {
    assert.equal(restartLogic.includes(`"${status}"`), true);
  }
  assert.equal(restartLogic.includes("sourceSelectionLocked"), true);
  assert.equal(sourceControl.includes("disabled={sourceSelectionLocked}"), true);
  assert.equal(sourceControl.includes('uploadLabel={canRestartTask ? "重新上传并新建任务" : undefined}'), true);
  assert.equal(sourceControl.includes("if (canRestartTask) resetTask();"), true);
  assert.equal(source.includes("selectedIds={canRestartTask ? [] : modelSource ? [modelSource.id] : []}"), true);
  assert.equal(source.includes("const nextSource = assets.find((asset) => asset.id === assetIds[0]);"), true);
  assert.equal(source.includes("if (!nextSource) {"), true);
  assert.equal(source.includes("setModelSource(nextSource);"), true);
  assert.equal(source.includes("<ImageDropZone"), true);
  const dropZone = await utf8("components/image-drop-zone.tsx");
  assert.equal(dropZone.includes('event.currentTarget.value = "";'), true);
  assert.equal(resetLogic.includes("setJobId(undefined);"), true);
  assert.equal(resetLogic.includes("setModelSource(undefined);"), true);
  assert.equal(resetLogic.includes("setSelectedPoseSlug(undefined);"), true);
  assert.equal(resetLogic.includes("setCustomPose(undefined);"), true);
  assert.equal(resetLogic.includes("sourceUpload.reset();"), true);
  assert.equal(resetLogic.includes("customPoseUpload.reset();"), true);
  assert.equal(source.includes("jobNote(job.data)"), true);
});

test("custom pose upload is exclusive with catalog selection and submission is fail closed", async () => {
  const source = await utf8(componentFile);

  assert.equal(occurrences(source, "useUploadAsset(workspace?.id)"), 2);
  assert.equal(source.includes("setCustomPose(asset);"), true);
  assert.equal(source.includes("setSelectedPoseSlug(undefined);"), true);
  assert.equal(source.includes('mode === "catalog"'), true);
  assert.equal(source.includes("workflow.canSubmit && workflow.canDeliverImages"), true);
  assert.equal(source.includes("!isSubmitting"), true);
  assert.equal(source.includes("!jobId"), true);
  assert.equal(source.includes("submissionLock.current"), true);
  assert.equal(source.includes("if (!canSubmit || !modelSource || submissionLock.current) return;"), true);
  assert.equal(source.includes("disabled={!canSubmit}"), true);
  assert.equal(source.includes("selectJobResultAssets(job.data)"), true);
  assert.equal(source.includes("<ResultGrid"), true);
  assert.equal(source.includes("data:image"), false);
});

test("new frontend files are valid UTF-8 without replacement placeholders", async () => {
  for (const file of [componentFile, cssFile, pageFile]) {
    const source = await utf8(file);
    assert.equal(source.includes("?".repeat(3)), false, file);
    assert.equal(source.includes(String.fromCharCode(0xfffd)), false, file);
  }
  assert.equal((await utf8(componentFile)).includes(" × "), true);
});
