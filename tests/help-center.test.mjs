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

test("help center preserves retention guidance and uses the shared customer service link", async () => {
  const [page, content, contact] = await Promise.all([
    utf8("app/help/page.tsx"),
    utf8("lib/help-center-content.ts"),
    utf8("components/customer-service-link.tsx"),
  ]);
  const combined = `${page}\n${content}`;

  assert.match(combined, /用户上传素材从上传成功起保存 30 天/);
  assert.match(combined, /生成图片从创建成功起保存 30 天/);
  assert.match(combined, /终态任务记录，从任务结束起保存 30 天/);
  assert.match(combined, /草稿从创建起保存 30 天/);
  assert.match(combined, /到期后相关内容不可恢复/);
  assert.match(page, /在线联系/);
  assert.match(page, /<QRCodeSVG value=\{CUSTOMER_SERVICE_URL\}/);
  assert.match(page, /<CustomerServiceLink className="primary-button"\s*\/>/);
  assert.match(page, /<h2 id="contact-modal-title">在线客服<\/h2>/);
  assert.match(contact, /https:\/\/work\.weixin\.qq\.com\/kfid\/kfc28d6af4de31987dc/);
  assert.match(contact, /href=\{CUSTOMER_SERVICE_URL\} target="_blank" rel="noopener noreferrer"/);
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
