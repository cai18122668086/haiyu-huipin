import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = readFileSync(new URL("../components/domestic-detail-approval.tsx", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: {
  jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
} });
const testModule = { exports: {} };
new Function("require", "module", "exports", outputText)(require, testModule, testModule.exports);
const { NativeApprovalDocument, DomesticDetailApproval } = testModule.exports;

test("complete native approval renders every row and safely escapes authored markup", () => {
  const document = "# 完整方案\n\n| 屏 | 内容 |\n|---|---|\n| M01 | 原始商品事实 |\n| M02 | 下屏独立职责 |\n\n## 双屏衔接\n所有逐屏内容保留\n<script>untrusted()</script>\n";
  const html = renderToStaticMarkup(createElement(NativeApprovalDocument, { document }));
  for (const value of ["完整方案", "M01", "原始商品事实", "M02", "下屏独立职责", "双屏衔接", "所有逐屏内容保留"]) assert.ok(html.includes(value));
  assert.ok(html.includes("&lt;script&gt;untrusted()&lt;/script&gt;"));
  assert.ok(!html.includes("<script>"));
  assert.equal((html.match(/<td /g) ?? []).length, 4);
});

test("download retains exact UTF-8 document and approval starts unchecked", () => {
  const document = "# 原生完整方案\n\n产品事实 500 ml\n";
  const html = renderToStaticMarkup(createElement(DomesticDetailApproval, {
    document, fingerprint: "a".repeat(64), reviewed: false, onReviewed: () => {},
  }));
  const download = html.match(/href="data:text\/markdown;charset=utf-8,([^"]*)"/);
  assert.ok(download);
  assert.equal(decodeURIComponent(download[1]), document);
  assert.ok(html.includes('type="checkbox"'));
  assert.ok(!html.includes('checked=""'));
});
