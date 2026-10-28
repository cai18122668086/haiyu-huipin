import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import ts from "typescript";

import { FEATURE_REGISTRY } from "../lib/feature-registry.ts";

const root = process.cwd();
const utf8 = (file) => readFile(path.join(root, file), "utf8");

const EXPECTED_FEATURE_CONTRACT = [
  ["local-edit", "/workspace/local-edit", "local_edit", "local_edit"],
  ["change-color", "/workspace/product-image/change-color", "product_image", "product_image.change_color"],
  ["change-background", "/workspace/product-image/change-background", "product_image", "product_image.change_background"],
  ["change-scene", "/workspace/product-image/change-scene", "product_image", "product_image.change_scene"],
  ["change-model", "/workspace/product-image/change-model", "product_image", "product_image.change_model"],
  ["scene-main", "/workspace/product-image/scene-main", "product_image", "product_image.scene_main"],
  ["white-background", "/workspace/product-image/white-background", "product_image", "product_image.white_background"],
  ["model-image", "/workspace/product-image/model-image", "product_image", "product_image.model_image"],
  ["try-on", "/workspace/product-image/try-on", "product_image", "product_image.try_on"],
  ["reference-remix", "/workspace/reference-remix", "reference_remix", "reference_remix"],
  ["one-click-replica", "/style-replica", "reference_remix", "reference_remix"],
  ["model-pose-transfer", "/workspace/model-pose-transfer", "model_pose_transfer", "model_pose_transfer"],
  ["creative-image", "/workspace/creative-image", "creative_image", "creative_image"],
  ["detail-pack", "/workspace/detail-pack", "detail_pack", "detail_pack"],
  ["domestic-main-image", "/workspace/domestic-main-image", "domestic_main_image", "domestic_main_image"],
  ["amazon-visual-suite", "/workspace/amazon-visual-suite", "amazon_visual_suite", "amazon_visual_suite"],
];

const AI_FEATURE_MENU_IDS = [
  "local-edit",
  "change-background",
  "reference-remix",
  "one-click-replica",
  "domestic-main-image",
  "detail-pack",
  "amazon-visual-suite",
];

const AI_TOOLBOX_MENU_IDS = [
  "change-color",
  "change-scene",
  "change-model",
  "scene-main",
  "white-background",
  "model-image",
  "try-on",
  "model-pose-transfer",
  "creative-image",
];

const HOME_TOOLBOX_IDS = [
  "change-background",
  "change-color",
  "change-scene",
  "change-model",
  "scene-main",
  "white-background",
  "model-image",
  "try-on",
  "model-pose-transfer",
  "creative-image",
];

const FEATURED_TASK_IDS = [
  "local-edit",
  "reference-remix",
  "one-click-replica",
  "detail-pack",
  "domestic-main-image",
  "amazon-visual-suite",
];

function parseTypescript(file, source) {
  return ts.createSourceFile(
    file,
    source,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
}

function findVariableInitializer(ast, variableName) {
  let initializer;

  const visit = (node) => {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.name.text === variableName
    ) {
      initializer = node.initializer;
      return;
    }
    ts.forEachChild(node, visit);
  };

  visit(ast);
  assert.ok(initializer, `${variableName} must remain a static declaration`);
  return initializer;
}

function collectCallStringArguments(node, functionName) {
  const values = [];
  const visit = (child) => {
    if (
      ts.isCallExpression(child) &&
      ts.isIdentifier(child.expression) &&
      child.expression.text === functionName &&
      child.arguments[0] &&
      ts.isStringLiteralLike(child.arguments[0])
    ) {
      values.push(child.arguments[0].text);
    }
    ts.forEachChild(child, visit);
  };
  visit(node);
  return values;
}

function collectObjectStringProperties(node, propertyName) {
  const values = [];
  const visit = (child) => {
    if (ts.isPropertyAssignment(child)) {
      const name = ts.isIdentifier(child.name) || ts.isStringLiteralLike(child.name)
        ? child.name.text
        : undefined;
      if (name === propertyName && ts.isStringLiteralLike(child.initializer)) {
        values.push(child.initializer.text);
      }
    }
    ts.forEachChild(child, visit);
  };
  visit(node);
  return values;
}

function assertUnique(values, label) {
  assert.equal(
    new Set(values).size,
    values.length,
    `${label} contains a duplicate feature id: ${values.join(", ")}`,
  );
}

function pngDimensions(buffer) {
  const pngSignature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  assert.equal(buffer.subarray(0, 8).equals(pngSignature), true, "asset must be a PNG file");
  assert.equal(buffer.subarray(12, 16).toString("ascii"), "IHDR", "PNG must contain IHDR first");
  return {
    width: buffer.readUInt32BE(16),
    height: buffer.readUInt32BE(20),
  };
}

test("feature IDs, routes, job types, and runtime capability keys remain unchanged", () => {
  assert.deepEqual(
    FEATURE_REGISTRY.map((feature) => [
      feature.id,
      feature.route,
      feature.jobType,
      feature.runtimeCapability,
    ]),
    EXPECTED_FEATURE_CONTRACT,
  );
});

test("the two navigation dropdowns are disjoint and resolve every item through the feature registry", async () => {
  const file = "lib/navigation-registry.ts";
  const source = await utf8(file);
  const ast = parseTypescript(file, source);
  const allFeatures = collectCallStringArguments(
    findVariableInitializer(ast, "AI_FEATURES_MENU"),
    "navigationFeature",
  );
  const toolbox = collectCallStringArguments(
    findVariableInitializer(ast, "AI_TOOLBOX_MENU"),
    "navigationFeature",
  );

  assert.deepEqual(allFeatures, AI_FEATURE_MENU_IDS);
  assert.deepEqual(toolbox, AI_TOOLBOX_MENU_IDS);
  assertUnique(allFeatures, "AI功能大全");
  assertUnique(toolbox, "AI工具箱");
  assert.deepEqual(
    allFeatures.filter((featureId) => toolbox.includes(featureId)),
    [],
    "the two dropdowns must not repeat a feature",
  );

  const registryById = new Map(FEATURE_REGISTRY.map((feature) => [feature.id, feature]));
  for (const featureId of [...allFeatures, ...toolbox]) {
    const feature = registryById.get(featureId);
    assert.ok(feature, `${featureId} must exist in FEATURE_REGISTRY`);
    assert.match(feature.route, /^(?:\/workspace\/|\/style-replica$)/, `${featureId} must keep a known workspace route`);
  }
  assert.equal(
    new Set([...allFeatures, ...toolbox]).size,
    FEATURE_REGISTRY.length,
    "the navigation menus should partition the known feature registry",
  );

  assert.match(source, /const feature = getFeatureById\(featureId\)/);
  assert.match(source, /href: feature\.route/);
  assert.match(source, /throw new Error\(`Navigation feature is missing/);

  const topNavigation = await utf8("components/top-navigation.tsx");
  assert.match(topNavigation, /ALL_TOOLS_MENU/);
  assert.match(topNavigation, /AI_NAVIGATION_MENUS/);
  assert.doesNotMatch(topNavigation, /const\s+navItems\s*=/);
});

test("the homepage AI toolbox excludes all one-click featured tasks", async () => {
  const file = "components/home-ai-toolbox.tsx";
  const source = await utf8(file);
  const ast = parseTypescript(file, source);
  const toolboxIds = collectObjectStringProperties(
    findVariableInitializer(ast, "TOOLBOX_DEFINITIONS"),
    "id",
  );
  const featuredIds = FEATURE_REGISTRY.filter((feature) => feature.featured).map(
    (feature) => feature.id,
  );

  assert.deepEqual(featuredIds, FEATURED_TASK_IDS);
  assert.deepEqual(toolboxIds, HOME_TOOLBOX_IDS);
  assertUnique(toolboxIds, "homepage AI toolbox");
  assert.deepEqual(
    toolboxIds.filter((featureId) => featuredIds.includes(featureId)),
    [],
    "homepage toolbox must not duplicate any 一键直达任务 item",
  );
  assert.equal(toolboxIds.includes("change-background"), true);
  assert.equal(toolboxIds.includes("model-pose-transfer"), true);

  assert.match(source, /getFeatureById\(item\.id\)/);
  assert.match(source, /href=\{feature\.route\}/);
  assert.match(source, /id="ai-toolbox"/);
});

test("Aurora domestic main image has discoverable entrypoints across the product", async () => {
  const route = "/workspace/domestic-main-image";
  const toolDirectory = await utf8("components/tool-directory.tsx");
  const navigation = await utf8("components/navigation-mega-menu.tsx");
  const toolsPage = await utf8("app/tools/page.tsx");
  const dashboard = await utf8("app/dashboard/page.tsx");
  const help = await utf8("app/help/page.tsx");
  const helpContent = await utf8("lib/help-center-content.ts");

  assert.equal(FEATURE_REGISTRY.find((feature) => feature.id === "domestic-main-image")?.featured, true);
  assert.doesNotMatch(toolDirectory, /home-aurora-entry/);
  assert.match(toolDirectory, /featuredTools\.map/);
  assert.match(navigation, /aria-current=\{active \? "page" : undefined\}/);
  assert.match(navigation, /pathname === item\.href \|\| pathname\.startsWith/);
  assert.match(toolsPage, /FEATURE_REGISTRY/);
  assert.match(dashboard, new RegExp(route.replaceAll("/", "\\/")));
  assert.match(help, /const faqs: Faq\[\] = \[/);
  assert.match(helpContent, /1 张搜索主图和 5 张轮播图/);
});

test("homepage search recommendations, hot cards, and task status cues stay scoped", async () => {
  const toolDirectory = await utf8("components/tool-directory.tsx");
  const topNavigation = await utf8("components/top-navigation.tsx");
  const navigation = await utf8("components/navigation-mega-menu.tsx");
  const featurePriorities = await utf8("lib/feature-priorities.ts");
  const styles = await utf8("app/focus-inspired.css");
  const globalStyles = await utf8("app/globals.css");

  assert.match(featurePriorities, /HOT_FEATURE_IDS = new Set\(\[\s*"local-edit",\s*"one-click-replica",\s*"detail-pack",\s*"domestic-main-image",\s*\]\)/s);
  assert.match(toolDirectory, /import \{ HOT_FEATURE_IDS \} from "@\/lib\/feature-priorities"/);
  assert.match(toolDirectory, /"local-edit", "reference-remix", "amazon-visual-suite"/);
  assert.match(toolDirectory, /<HaiyuStartHub \/>/);
  assert.match(toolDirectory, /HOT_FEATURE_IDS\.has\(tool\.id\)/);
  assert.match(toolDirectory, /pattern: \/亚马逊\|amazon\|跨境\|a\\\+\/iu[\s\S]*?featureIds: \["amazon-visual-suite", "detail-pack", "white-background"\]/);
  assert.match(toolDirectory, /pattern: \/主图\|淘宝\|天猫\|京东\|轮播\/iu[\s\S]*?featureIds: \["domestic-main-image", "scene-main", "white-background"\]/);
  assert.match(toolDirectory, /role=\{searchResults\.length \? "listbox" : "region"\}/);
  assert.match(toolDirectory, /aria-label=\{searchResults\.length \? "功能搜索结果" : "相近场景推荐"\}/);
  assert.match(toolDirectory, /home-search-recommendation/);
  assert.match(toolDirectory, /home-command home-command--primary/);
  assert.match(toolDirectory, /<HaiyuHero \/>[\s\S]*?home-command home-command--primary/);
  assert.match(toolDirectory, /event\.key === "Enter" && searchResults\.length/);
  assert.match(styles, /\.home-command--primary > label\s*\{[\s\S]*?min-height: 96px;/);
  assert.match(styles, /\.home-search-recommendation\.is-unavailable/);
  assert.match(styles, /\.home-feature-card__hot\s*\{/);
  assert.match(styles, /\.home-feature-card__hot svg\s*\{[\s\S]*?width: 23px;/);
  assert.match(styles, /@media \(min-width: 1200px\)\s*\{[\s\S]*?\.home-feature-grid\s*\{[\s\S]*?grid-template-columns: repeat\(3, minmax\(0, 1fr\)\);[\s\S]*?grid-template-rows: repeat\(2, var\(--home-feature-grid-row\)\);/);
  assert.match(navigation, /import \{ HOT_FEATURE_IDS \} from "@\/lib\/feature-priorities"/);
  assert.match(navigation, /const isHot = HOT_FEATURE_IDS\.has\(item\.featureId\);/);
  assert.match(navigation, /nav-tool-link__hot/);
  assert.match(globalStyles, /\.nav-tool-link__hot\s*\{/);

  assert.match(topNavigation, /const errorCount =/);
  assert.match(topNavigation, /const taskStateClass = activeCount \? "is-active"/);
  assert.doesNotMatch(topNavigation, /taskLabel = errorCount \? "异常"/);
  assert.match(topNavigation, /className=\{`task-trigger \$\{taskStateClass\}`\}/);
  assert.match(topNavigation, /const taskAriaLabel = activeCount/);
  assert.match(topNavigation, /aria-label=\{taskAriaLabel\}/);
  assert.match(styles, /\.task-trigger\.is-active\s*\{/);
  assert.match(styles, /\.task-trigger\.has-error\s*\{/);
});
test("homepage and navigation public copy no longer exposes internal implementation wording", async () => {
  const publicFiles = [
    "components/app-shell.tsx",
    "components/top-navigation.tsx",
    "components/tool-directory.tsx",
    "components/navigation-mega-menu.tsx",
    "components/home-ai-toolbox.tsx",
    "components/home-banner-carousel.tsx",
  ];
  const forbidden = [
    ["内部 mock", /内部\s*mock/iu],
    ["0付费", /0\s*付费/iu],
    ["owner", /\bowner\b/iu],
    ["Beta", /\bBeta\b/iu],
    ["Provider", /\bProvider\b/iu],
    ["Provider/runtime combination", /(?:Provider[\s\S]{0,40}runtime|runtime[\s\S]{0,40}Provider)/iu],
    ["internal capability wording", /内部(?:能力|测试|环境|版本)/iu],
    ["backend-driven wording", /后端驱动/iu],
    ["Job contract", /Job\s*contract/iu],
  ];

  for (const file of publicFiles) {
    const source = await utf8(file);
    for (const [label, pattern] of forbidden) {
      assert.doesNotMatch(source, pattern, `${file} exposes forbidden public copy: ${label}`);
    }
  }

  const appShell = await utf8("components/app-shell.tsx");
  assert.doesNotMatch(appShell, /RuntimeCapabilityBanner/);

  const topNavigation = await utf8("components/top-navigation.tsx");
  assert.doesNotMatch(topNavigation, /workspace\?\.role/);
  assert.match(topNavigation, /个人控制台/);
  assert.match(topNavigation, /个人中心/);
});

test("the exact two desktop and two mobile homepage banners are present", async () => {
  const bannerDirectory = path.join(root, "public", "homepage-banners");
  const expected = new Map([
    ["polaris-home-banner-01-v12-fresh-1920x240.png", { width: 1920, height: 240 }],
    ["polaris-home-banner-02-v12-fresh-1920x240.png", { width: 1920, height: 240 }],
    ["polaris-mobile-banner-01-v1-750x300.png", { width: 750, height: 300 }],
    ["polaris-mobile-banner-02-v1-750x300.png", { width: 750, height: 300 }],
  ]);
  const pngFiles = (await readdir(bannerDirectory))
    .filter((file) => file.toLowerCase().endsWith(".png"))
    .sort();

  assert.deepEqual(pngFiles, [...expected.keys()].sort());
  for (const [file, dimensions] of expected) {
    const buffer = await readFile(path.join(bannerDirectory, file));
    assert.deepEqual(pngDimensions(buffer), dimensions, `${file} dimensions changed`);
  }

  const carousel = await utf8("components/home-banner-carousel.tsx");
  for (const file of expected.keys()) {
    assert.match(carousel, new RegExp(file.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(carousel, /<picture>/);
  assert.match(carousel, /max-width:\s*720px/);
});

