import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const utf8 = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

test("help center search suggests matching FAQs and opens the selected answer", async () => {
  const page = await utf8("app/help/page.tsx");

  assert.match(page, /value=\{searchQuery\}/);
  assert.match(page, /onChange=\{\(event\) => setSearchQuery\(event\.target\.value\)\}/);
  assert.match(page, /event\.metaKey \|\| event\.ctrlKey/);
  assert.match(page, /filteredFaqs/);
  assert.match(page, /role="listbox"/);
  assert.match(page, /selectSearchResult\(index\)/);
  assert.match(page, /scrollIntoView\(\{ behavior: "smooth", block: "center" \}\)/);
  assert.match(page, /如何上传一句话改图的原始图片/);
  assert.match(page, /上传图片支持哪些格式和大小/);
  assert.match(page, /上传后图片没有显示怎么办/);
  assert.match(page, /上传多角度商品图时应该如何分组/);
});

test("help center removes the crowded quick-start and feature-picker sections and uses a guide dialog", async () => {
  const page = await utf8("app/help/page.tsx");

  assert.doesNotMatch(page, /id="quick-start"/);
  assert.doesNotMatch(page, /id="all-features"/);
  assert.doesNotMatch(page, /FEATURE_GROUPS/);
  assert.match(page, /setGuideOpen\(true\)/);
  assert.match(page, /aria-labelledby="guide-modal-title"/);
  assert.match(page, /唯一需要涂选/);
  assert.match(page, /不需要手动涂选/);
  assert.match(page, /详情图 \/ 亚马逊套图/);
  assert.match(page, /按功能完成创作/);
});

test("help center preserves retention guidance and uses online WeChat contact", async () => {
  const [page, content] = await Promise.all([
    utf8("app/help/page.tsx"),
    utf8("lib/help-center-content.ts"),
  ]);
  const combined = `${page}\n${content}`;

  assert.match(combined, /用户上传素材从上传成功起保存 30 天/);
  assert.match(combined, /生成图片从创建成功起保存 30 天/);
  assert.match(combined, /终态任务记录，从任务结束起保存 30 天/);
  assert.match(combined, /草稿从创建起保存 30 天/);
  assert.match(combined, /到期后相关内容不可恢复/);
  assert.match(page, /在线联系/);
  assert.match(page, /haiyu-contact-qr\.jpg/);
  assert.match(page, /扫码添加微信客服/);
  assert.match(page, /https:\/\/work\.weixin\.qq\.com\/kfid\/kfc1f8fc41acaca0895/);
  assert.doesNotMatch(page, /mailto:/);
  assert.doesNotMatch(page, /发送问题邮件/);
});

test("mobile primary navigation includes a direct help-center entry", async () => {
  const [registry, navigation] = await Promise.all([
    utf8("lib/navigation-registry.ts"),
    utf8("components/top-navigation.tsx"),
  ]);

  assert.match(registry, /MOBILE_PRIMARY_NAVIGATION_LINKS/);
  assert.match(registry, /\{ label: "帮助中心", href: "\/help" \}/);
  assert.match(navigation, /primaryLinks=\{MOBILE_PRIMARY_NAVIGATION_LINKS\}/);
});
