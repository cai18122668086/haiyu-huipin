import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const require = createRequire(import.meta.url);
function load(file, resolver = require) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(file, import.meta.url), "utf8"), { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  const mod = { exports: {} }; new Function("require", "module", "exports", outputText)(resolver, mod, mod.exports); return mod.exports;
}
const brand = load("../lib/detail-brand.ts");
const { DomesticDetailIntakeQuestion } = load("../components/domestic-detail-intake-question.tsx", name => {
  if (name === "@/features/jobs/api/jobs") return { useContinueDetailBrand: () => ({ mutate() {} }), useResolveDetailIntake: () => ({ mutate() {} }) };
  if (name === "@/lib/api/request") return { errorMessage: String };
  if (name === "@/lib/detail-brand") return brand;
  return require(name);
});
function render(intake) {
  return renderToStaticMarkup(createElement(DomesticDetailIntakeQuestion, { workspaceId: "ws", job: { id: "job", allowed_actions: ["resolve_intake"], plan: { revision: 1, payload: { product_intake: intake } } } }));
}
test("brand questions render automatic progress with no brandless declaration or upload choice", () => {
  const html = render({ brand_confirmation_required: true, source_conflicts: [] });
  assert.match(html, /完成后自动继续/);
  for (const text of [/type="checkbox"/, /type="file"/, /我确认当前商品无品牌/, /上传.*品牌/, /确认商品信息并继续/]) assert.doesNotMatch(html, text);
});
test("a background-brand question is automatic while specification choices remain available", () => {
  const intake = { observed_facts: [{ id: "O1", source_class: "OBSERVED_PRODUCT_EVIDENCE", text: "商品品牌标识为甲" }, { id: "O2", source_class: "OBSERVED_PRODUCT_EVIDENCE", text: "背景包装可见其他品牌字样" }], source_conflicts: [{ id: "C1", topic: "品牌与包装SKU身份", alternative_fact_ids: ["O1", "O2"] }] };
  assert.equal(brand.needsAutomaticBrandHandling(intake), true);
  assert.doesNotMatch(render(intake), /type="radio"/);
  const specifications = { user_facts: [{ id: "F1", text: "净含量300ml" }, { id: "F2", text: "净含量500ml" }], source_conflicts: [{ id: "C2", topic: "净含量", why_material: "两种容量", alternative_fact_ids: ["F1", "F2"] }] };
  const html = render(specifications);
  assert.equal(brand.needsAutomaticBrandHandling(specifications), false);
  assert.match(html, /净含量300ml/); assert.match(html, /type="radio"/);
});
