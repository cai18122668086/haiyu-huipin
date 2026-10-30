import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const root = process.cwd();
const utf8 = (file) => readFile(path.join(root, file), "utf8");

test("all new standalone feature routes render the shared commerce workspace", async () => {
  const routes = {
    "app/workspace/reference-remix/page.tsx": "reference-remix",
    "app/workspace/creative-image/page.tsx": "creative-image",
    "app/workspace/amazon-visual-suite/page.tsx": "amazon-visual-suite",
  };
  for (const [file, featureId] of Object.entries(routes)) {
    const source = await utf8(file);
    assert.match(source, /CommerceSkillWorkspace/);
    assert.match(source, new RegExp(`featureId="${featureId}"`));
  }
});

test("standalone workspaces submit the exact backend job families", async () => {
  const workspace = await utf8("components/commerce-skill-workspace.tsx");
  for (const jobType of [
    "reference_remix",
    "creative_image",
    "amazon_visual_suite",
  ]) {
    assert.match(workspace, new RegExp(`type: "${jobType}"`));
  }
  assert.match(workspace, /product_asset_ids: selections\.product/);
  assert.match(workspace, /reference_asset_ids: selections\.reference/);
  assert.match(workspace, /competitor_asset_ids: selections\.competitor/);
  assert.match(
    workspace,
    /inspiration_reference_planning: selections\.competitor\.length > 0/,
  );
  const remixBody = workspace.slice(
    workspace.indexOf('type: "reference_remix"'),
    workspace.indexOf(
      'if (featureId === "creative-image")',
      workspace.indexOf('type: "reference_remix"'),
    ),
  );
  assert.match(remixBody, /reference_skill: "aurora-remix-fast"/);
  assert.match(remixBody, /inspiration_product_first: true/);

  const amazonBody = workspace.slice(
    workspace.indexOf('type: "amazon_visual_suite"'),
    workspace.indexOf("const submit = async"),
  );
  const amazonSourcePayload = {
    white_background_product_asset_ids: "whiteBackground",
    detail_asset_ids: "detail",
    package_asset_ids: "package",
    scene_asset_ids: "scene",
    brand_asset_ids: "brand",
    reference_asset_ids: "reference",
    competitor_reference_asset_ids: "competitor",
  };
  for (const [field, role] of Object.entries(amazonSourcePayload)) {
    assert.match(amazonBody, new RegExp(`${field}: selections\\.${role}`));
  }
});
test("creative and reference workspaces preserve the full count and aspect-ratio controls", async () => {
  const workspace = await utf8("components/commerce-skill-workspace.tsx");
  assert.match(workspace, /const \[draftImageCount, setImageCount\] = useDraftState\(workspace\?\.id, featureId, "draftImageCount", 1\)/);
  assert.match(workspace, /const imageCount = jobId \? saved\.number\("image_count"/);
  assert.match(workspace, /image_count: imageCount/);
  assert.doesNotMatch(workspace, /image_count: Math\.min/);
  assert.match(workspace, /Array\.from\(\{ length: 12 \}/);
  assert.match(workspace, /QuantityControl[^>]*onChange=\{setImageCount\}[^>]*allowedValues=\{imageCountOptions\}/);
  assert.match(workspace, /value: "4:5"/);
  assert.match(workspace, /value: "follow-reference"/);
  assert.match(workspace, /featureId === "reference-remix" \? "follow-reference" : "1:1"/);
  for (const size of [
    "1024 × 1024",
    "768 × 1024",
    "1024 × 1280",
    "1024 × 768",
    "576 × 1024",
    "1024 × 576",
  ]) {
    assert.match(workspace, new RegExp(size));
  }
  assert.match(workspace, /RatioControl[^\n]*options=\{featureId === "reference-remix"/);
  assert.match(workspace, /GenerationVersionField name="amazon-suite-speed"/);
});

test("inspiration automatic ratio remains dynamic instead of silently becoming square", async () => {
  const inspiration = await utf8("components/inspiration-gallery-db-v2.tsx");
  assert.match(inspiration, /if \(value === "自动"\) return "follow-reference"/);
  assert.match(inspiration, /aspect_ratio: inspirationAspectToApi\(aspectRatio\)/g);
  assert.doesNotMatch(inspiration, /if \(value === "自动" \|\| value === "1:1"\) return "1:1"/);
});

test("commerce workspaces do not require a guessed product name", async () => {
  const workspace = await utf8("components/commerce-skill-workspace.tsx");
  assert.match(workspace, /product_name: productName\.trim\(\)/);
  assert.doesNotMatch(workspace, /PRODUCT_NAME_NOT_PROVIDED/);
  assert.doesNotMatch(workspace, /amazonProductNameMissing/);
  assert.doesNotMatch(workspace, /Amazon 套图必须填写商品名称/);
});

test("Amazon fixed pack delegates all 21 JobItems to the backend", async () => {
  const workspace = await utf8("components/commerce-skill-workspace.tsx");
  const amazonPlatforms = workspace.slice(
    workspace.indexOf("const amazonPlatforms"),
    workspace.indexOf("const creativeDirections"),
  );
  const amazonBody = workspace.slice(
    workspace.indexOf('type: "amazon_visual_suite"'),
    workspace.indexOf("const submit = async"),
  );
  assert.match(workspace, /const expectedCount = isAmazon[\s\S]*\? 21/);
  assert.match(workspace, /预计生成 21 张套图/);
  assert.match(amazonPlatforms, /value: "amazon-us"/);
  assert.doesNotMatch(amazonPlatforms, /tmall|jd|douyin|xiaohongshu|generic/);
  assert.match(workspace, /isAmazon \? amazonPlatforms : platforms/);
  assert.doesNotMatch(amazonBody, /image_count/);
  assert.doesNotMatch(amazonBody, /item_key|MAIN-01|SECONDARY-01|A-DESKTOP-01/);
});

test("Amazon keeps one primary source role, one general reference role, and five classified roles", async () => {
  const workspace = await utf8("components/commerce-skill-workspace.tsx");

  const readRoleKeys = (constantName) => {
    const declaration = workspace.match(
      new RegExp(
        "const " + constantName + "[^=]*=\\s*\\[([\\s\\S]*?)\\]",
      ),
    );
    assert.ok(declaration, constantName + " must declare the Amazon role-key list");
    return [...declaration[1].matchAll(/"([A-Za-z]+)"/g)].map((match) => match[1]);
  };

  assert.deepEqual(readRoleKeys("AMAZON_PRIMARY_ROLE_KEYS"), ["whiteBackground"]);
  assert.deepEqual(readRoleKeys("AMAZON_REFERENCE_ROLE_KEYS"), ["reference"]);
  assert.deepEqual(readRoleKeys("AMAZON_ADVANCED_ROLE_KEYS"), [
    "detail",
    "package",
    "scene",
    "brand",
    "competitor",
  ]);
});

test("Amazon simplified source roles share the backend eight-image limit", async () => {
  const workspace = await utf8("components/commerce-skill-workspace.tsx");

  assert.match(workspace, /const AMAZON_SOURCE_ASSET_LIMIT = 8/);
  assert.match(workspace, /素材合计最多 \$\{AMAZON_SOURCE_ASSET_LIMIT\} 张/);
  assert.doesNotMatch(workspace, /七类源图/);
  assert.match(
    workspace,
    /selectedIds\.length[\s\S]*AMAZON_SOURCE_ASSET_LIMIT/,
  );
  assert.match(
    workspace,
    /selectedIds\.length[\s\S]*AMAZON_SOURCE_ASSET_LIMIT/,
  );
  assert.match(
    workspace,
    /const availableSlots = isAmazon[\s\S]*AMAZON_SOURCE_ASSET_LIMIT - selectedIds\.length[\s\S]*AMAZON_SOURCE_ASSET_LIMIT\) - selections\[role\]\.length/,
  );
  assert.match(workspace, /validateImageUpload\(files, \{ multiple: true, maxFiles: availableSlots \}\)/);
  assert.match(
    workspace,
    /maxFiles=\{amazonRemainingSourceSlots \?\? Math\.max\(0, \(featureId === "reference-remix" \? 1 : AMAZON_SOURCE_ASSET_LIMIT\) - selections\[role\.key\]\.length\)\}/,
  );
  assert.match(
    workspace,
    /maxItems=\{[\s\S]*isAmazon[\s\S]*selections\[activeLibraryRole\.key\]\.length \+ \(amazonRemainingSourceSlots \?\? 0\)[\s\S]*: featureId === "reference-remix" \? 1 : AMAZON_SOURCE_ASSET_LIMIT/,
  );
  assert.match(
    workspace,
    /const roleLimit = isAmazon[\s\S]*AMAZON_SOURCE_ASSET_LIMIT - otherRoleCount[\s\S]*: featureId === "reference-remix" \? 1 : AMAZON_SOURCE_ASSET_LIMIT/,
  );
});

test("Amazon form exposes only three concise Chinese content fields", async () => {
  const workspace = await utf8("components/commerce-skill-workspace.tsx");
  const amazonForm = workspace.slice(
    workspace.indexOf('<div className={amazonStyles.amazonForm}>'),
    workspace.indexOf("              ) : (", workspace.indexOf('<div className={amazonStyles.amazonForm}>')),
  );

  for (const copy of [
    "上传商品素材",
    "添加参考图片",
    "按用途分类素材",
    "告诉我们这个商品",
    "商品名称",
    "商品卖点与参数",
    "品牌与生成要求",
  ]) {
    assert.match(workspace, new RegExp(copy));
  }

  assert.match(amazonForm, /value=\{productName\}/);
  assert.match(amazonForm, /value=\{productFacts\}/);
  assert.match(amazonForm, /value=\{instruction\}/);
  assert.equal((amazonForm.match(/<label className=\{amazonStyles\.simpleField\}>/g) ?? []).length, 3);

  for (const hiddenField of [
    "targetBuyer",
    "listingDescription",
    "listingTitle",
    "aPlusCopy",
    "parameters",
    "certificationsOrEvidence",
    "forbiddenClaims",
    "brandColors",
    "stylePreferences",
    "sellingPoints",
    "category",
    "language",
  ]) {
    assert.doesNotMatch(amazonForm, new RegExp(`value=\\{${hiddenField}\\}`));
  }
  assert.doesNotMatch(workspace, />更多信息</);
  assert.doesNotMatch(
    amazonForm,
    /Listing|Bullet Points|Buyer Demand Map|Campaign Style Lock|Contact Sheet/,
  );

  // The UI is simplified without changing the backend contract or Skill capability.
  const amazonBody = workspace.slice(
    workspace.indexOf('type: "amazon_visual_suite"'),
    workspace.indexOf("const submit = async"),
  );
  for (const field of [
    "marketplace",
    "product_name",
    "category",
    "target_buyer",
    "listing_title",
    "bullet_points",
    "listing_description",
    "a_plus_copy",
    "product_facts",
    "parameters",
    "certifications_or_evidence",
    "forbidden_claims",
    "brand_colors",
    "style_preferences",
    "instruction",
    "language",
  ]) {
    assert.match(amazonBody, field === "language" ? /language,/ : new RegExp(`${field}:`));
  }
});

test("Amazon automatically generates after planning and retains historical confirmation", async () => {
  const workspace = await utf8("components/commerce-skill-workspace.tsx");
  const jobApi = await utf8("features/jobs/api/jobs.ts");

  assert.match(workspace, /readAmazonPlanPages\(job\.data\?\.plan\?\.payload\)/);
  assert.match(workspace, /auto_start_after_plan: true/);
  assert.match(workspace, /isAmazon && !automaticAmazonPlan && job\.data\?\.status/);
  assert.match(workspace, /完成校验后将自动生成 21 张图片/);
  assert.match(workspace, /job\.data\?\.status === "awaiting_confirmation"/);
  assert.match(workspace, /确认 21 项页面规划/);
  assert.match(workspace, /amazonPlanPages\.map/);
  assert.match(workspace, /useConfirmPlan\(workspace\?\.id\)/);
  assert.match(workspace, /allowsJobAction\(job\.data\.allowed_actions, "confirm_plan"\)/);
  assert.match(workspace, /revision: job\.data\.plan\.revision/);
  assert.match(workspace, /确认计划并开始生成/);
  assert.match(jobApi, /invalidateQueries/);
  assert.match(jobApi, /if \(status && isActiveJob\(status\)\) return 1_500;/);
});

test("Amazon distinguishes technical completion from business quality review", async () => {
  const workspace = await utf8("components/commerce-skill-workspace.tsx");
  assert.match(workspace, /readAmazonBusinessQuality/);
  assert.match(workspace, /技术交付完成，业务质量等待人工审核/);
  assert.match(workspace, /title: "业务质量检查未通过"/);
  assert.match(workspace, /仅状态为已完成且可交付的图片可下载，请查看失败项并按需重试/);
  assert.doesNotMatch(workspace, /文件仍可下载核对，但不会自动重做/);
  assert.match(workspace, /review_infrastructure_failed/);
  assert.match(
    workspace,
    /Object\.prototype\.hasOwnProperty\.call\(AMAZON_BUSINESS_QUALITY_COPY, status\)/,
  );
  assert.match(workspace, /jobStatus === "completed"/);
  assert.match(
    workspace,
    /jobStatus === "partial" &&[\s\S]*qualityStatus === "failed" \|\| qualityStatus === "review_infrastructure_failed"/,
  );
  assert.match(workspace, /图片已生成，但自动视觉审核基础设施失败/);
  assert.match(workspace, /本次自动审核不可用，不代表图片质量未通过/);
});

test("Amazon review infrastructure failure preserves generated-image semantics", async () => {
  const workspace = await utf8("components/commerce-skill-workspace.tsx");

  assert.match(
    workspace,
    /isAmazon &&[\s\S]*job\.data\?\.status === "partial" &&[\s\S]*amazonBusinessQuality\?\.status === "review_infrastructure_failed" &&[\s\S]*job\.data\.items\.some/,
  );
  assert.match(
    workspace,
    /const expectedCount = isAmazon[\s\S]*\? 21[\s\S]*job\.data\?\.expected_item_count \?\? imageCount/,
  );
  assert.match(workspace, /const generatedCount = resultAssets\.length/);
  assert.match(
    workspace,
    /generatedCount === expectedCount[\s\S]*\$\{generatedCount\} 张图片已生成，自动视觉审核未完成[\s\S]*\$\{generatedCount\} \/ \$\{expectedCount\} 张图片已生成，自动视觉审核未完成/,
  );
  assert.match(
    workspace,
    /itemStatus === "failed"[\s\S]*errorDetails\?\.failure_kind === "review_infrastructure_failure"[\s\S]*errorDetails\.image_quality_failed === false[\s\S]*errorDetails\.generated_asset_preserved === true/,
  );
  assert.match(
    workspace,
    /isAmazonReviewInfrastructureFailure &&[\s\S]*isPreservedAmazonReviewInfrastructureBlocker\([\s\S]*item\.status,[\s\S]*item\.error_details,[\s\S]*\)/,
  );
  assert.match(
    workspace,
    /data-status=\{isPreservedReviewBlocker \? "succeeded" : item\.status\}/,
  );
  assert.match(workspace, /图片已生成，自动视觉审核未完成/);
  assert.match(
    workspace,
    /const canDownloadAmazonArchive = Boolean\([\s\S]*resultAssets\.length === expectedCount[\s\S]*job\.data\?\.status === "completed" \|\| isAmazonReviewInfrastructureFailure/,
  );
  assert.match(
    workspace,
    /archiveJobId=\{canDownloadAmazonArchive \? job\.data\?\.id : undefined\}/,
  );
  assert.match(
    workspace,
    /suppressFailureDetails=\{isAmazonReviewInfrastructureFailure\}/,
  );
  assert.match(
    workspace,
    /return `\$\{job\.data\.succeeded_item_count\} \/ \$\{job\.data\.expected_item_count\}/,
  );
});

test("new workspaces are runtime gated and never fabricate output", async () => {
  const workspace = await utf8("components/commerce-skill-workspace.tsx");
  const runtime = await utf8("features/runtime/api/runtime.ts");
  assert.match(workspace, /resolveRuntimeCapability/);
  assert.match(workspace, /workflow\.canSubmit/);
  assert.match(workspace, /disabled=\{!canSubmit\}/);
  assert.match(workspace, /selectJobResultAssets\(job\.data\)/);
  assert.match(runtime, /execution === "mock"/);
  assert.match(runtime, /canSubmit: true/);
  assert.match(runtime, /reasonCode: capability\.reason_code/);
  assert.doesNotMatch(workspace, /data:image|placeholder.*asset|mock.*image/i);
});

test("public navigation uses registry route matching instead of local-edit fallback", async () => {
  const toolRegistry = await utf8("lib/tool-registry.ts");
  const sidebar = await utf8("components/tool-sidebar.tsx");
  const navigation = await utf8("components/top-navigation.tsx");
  const navigationRegistry = await utf8("lib/navigation-registry.ts");
  const directory = await utf8("components/tool-directory.tsx");

  assert.match(toolRegistry, /FEATURE_GROUPS\.map/);
  assert.match(sidebar, /getFeatureByRoute\(pathname\)/);
  assert.match(sidebar, /currentFeature\?\.jobType/);
  assert.match(sidebar, /currentFeature\?\.route \?\? "\/tools"/);
  assert.doesNotMatch(sidebar, /pathname\.includes\("detail-pack"\) \? "detail_pack" : "local_edit"/);
  assert.match(navigation, /ALL_TOOLS_MENU/);
  assert.match(navigationRegistry, /href: "\/tools"/);
  assert.doesNotMatch(navigation, /\/\#tools/);
  assert.match(directory, /FEATURE_REGISTRY\.map/);
  assert.match(directory, /searchableTools\.find/);
});

test("detail workspace automatically starts submitted suite plans and retains historical confirmation", async () => {
  const detail = await utf8("app/workspace/detail-pack/page.tsx");
  assert.match(detail, /DOMESTIC_DETAIL_SIZE: MarketplaceImageSize = "1440x1920"/);
  assert.match(detail, /isRefinedDetail \? "3:4 原图交付" : `严格交付 \$\{imageSize\}`/);
  assert.match(detail, /\{selectedMarketplace\.name\} · \{isRefinedDetail \? "3:4 · 实际尺寸见结果" : imageSize\}/);
  assert.match(detail, /market_region: marketRegion/);
  assert.match(detail, /auto_start_after_plan: true/);
  assert.match(detail, /!automaticPlan && job\.data\?\.plan && workflowAvailable/);
  assert.match(detail, /生成整套详情图/);
  assert.doesNotMatch(detail, /market_region: "international" as const/);
  assert.match(detail, /selectRegion\("domestic"\).*disabled=\{Boolean\(jobId\)\}/);
  assert.match(detail, /selectRegion\("international"\).*disabled=\{Boolean\(jobId\)\}/);
});

test("completed detail and Amazon packs expose real attachment and ZIP downloads", async () => {
  const assetApi = await utf8("features/assets/api/assets.ts");
  const jobApi = await utf8("features/jobs/api/jobs.ts");
  const result = await utf8("components/job-result-image.tsx");
  const archive = await utf8("components/job-deliverables-download.tsx");
  const detail = await utf8("app/workspace/detail-pack/page.tsx");
  const commerce = await utf8("components/commerce-skill-workspace.tsx");

  assert.match(assetApi, /disposition: "inline" \| "attachment"/);
  assert.match(result, /getAssetAccessUrl[\s\S]*"attachment"/);
  assert.doesNotMatch(result, /href=\{access\.data\.url\} download/);
  assert.match(jobApi, /jobs\/\{job_id\}\/deliverables\.zip/);
  assert.match(archive, /下载整套 ZIP/);
  assert.match(detail, /JobDeliverablesDownload/);
  assert.match(commerce, /archiveJobId=\{[\s\S]*isAmazon/);
});
