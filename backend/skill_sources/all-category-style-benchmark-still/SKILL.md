---
name: all-category-style-benchmark-still
description: Generate original ecommerce visuals by inheriting reference style mechanisms while rebuilding the current product world. Supports typed search covers, carousels, detail pages, selling-point cards, promo posters, banners, social commerce cards and marketing sets.
---

# Aurora high-fidelity style adaptation（4.0.0 主版）

Use a supplied current-product image as the only product identity source. Use benchmark images as evidence for reusable visual mechanisms: typography morphology, hand-drawn material, composition rhythm, information hierarchy, decorative carriers, camera/light/depth, density and reading path. Rebuild the product world around confirmed product facts; never replace the source product inside its original scene.

## Mandatory activation gate

When this skill is named, linked, or clearly applicable to an ecommerce visual request, enter the executable workflow before planning copy or calling any image provider. The first operational action is always to create or resume a persisted run:

```text
py -3.12 scripts/entrypoint.py new --base-dir <run-parent> --product-input <product-file> --reference-input <benchmark-file>
```

Use `--input` only when roles are intentionally mixed and will be reviewed by `inspect-inputs`. Then follow the `next_action` returned by the run. Do not run the artflow provider (`scripts/artflow_render.py`), call any other image API, or write a hand-written image prompt before the run has produced a passing `audit-plan`, an active `generation-request-*.json`, and `GENERATION_ALLOWED`. Reading this file or a reference is not workflow execution. `--help`, code search, or a verbal plan do not satisfy the gate. If the workflow cannot start, stop and report the exact missing input or command error instead of generating directly.

## Required workflow

Run the executable workflow in `scripts/entrypoint.py`:

```text
new → register-input → inspect-inputs (when uploads are mixed) → classify-route
→ init-reference-analysis → crop-reference-panels → compile-reference-crops
→ compile-visual-passport → register-product-facts → plan-target-page
→ audit-plan → generation → artwork record → visual/product/originality audit
→ one targeted revision if needed → accept → global audit → route delivery
```

The workflow is fail-closed. It requires physical sanitized style-only crops, fresh mechanism observations, product facts with evidence, an explicit semantic translation for source semantics, a route plan, a single-use generation token, an existing artwork file with SHA-256, and an explicit audit receipt. Imagegen handoff is also fail-closed: run `scripts/artflow_render.py --workspace <run> --request <run>/artifacts/generation-request-Gxx.json --output <run>/actual.png`; it refuses requests unless the persisted run is `GENERATION_ALLOWED`, has a passing `audit-plan`, and the request is the active target. A persisted `generation-block.json` is a hard stop even if another process incorrectly wrote `GENERATION_ALLOWED`; clear it only by rerunning the plan stage and obtaining a fresh passing audit. `scripts/imagegen_adapter.py` builds the same validated handoff without rendering, and `scripts/artflow_render.py` is the only permitted bridge to the image provider: it runs that identical gate and then renders through the configured artflow endpoint. Do not call the provider, any other image API, or a hand-written prompt directly, and do not render after a workflow error.

## Native high-resolution text and image rendering

Every generation request requires `quality: high`. For detail Pair masters, the geometry contract is width:height `3:8`; pixel dimensions are negotiated with the native tool and are not a fixed 1440×3840 requirement. Use the clearest native output available and never enlarge or sharpen a smaller file to satisfy delivery. All visible copy, labels, measurements and product imagery must be rendered together by the image generation model in one pass. Post-generation text overlays, HTML typesetting and compositing text onto the raster are forbidden.

For a detail Pair, the master is one full-width vertical canvas: the two bound screens occupy the upper and lower halves with one horizontal seam. A left/right split, vertical divider, long-column reinterpretation or extra content added to fill the canvas is a geometry and density failure. The product itself must not acquire an unconfirmed brand name, series name, product name, logo, watermark or legible wordmark; use blank material or unreadable texture unless the exact mark is a confirmed product fact.

The generation request records this prohibition as `ban_unconfirmed_product_marks`; a visual audit records `unconfirmed_product_mark` when it observes such contamination, and that target cannot pass until revised.

Visible copy must not use handwritten, brush, calligraphic, marker or cursive lettering like the supplied example. This form ban has priority over reference-font inheritance: keep the reference's allowed typography mechanisms such as proportion, weight, spacing, hierarchy and carrier, while replacing only the forbidden letterform. This is a typography-form ban only: retain all benchmark-matched text, specifications, evidence, usage instructions and information density.

Visual fidelity is checked mechanism by mechanism. The generation request must carry a style signature for typography, material, composition, hierarchy, carriers, light/depth, density and reading path. A real visual audit must provide a score of at least 0.8 for every mechanism; a single overall “style similarity” score is insufficient. Generic blue-tech or generic ecommerce styling cannot substitute for the observed reference grammar.

## Global label-form ban and reference-derived density

Every route bans only AI-looking label carriers: do not package copy inside pills, capsules, chips, badges, stickers, label clouds or repeated rounded cards, and do not replace the reference's information layout with repeated boxed icon-and-caption components. If a benchmark uses one of these carriers, preserve its information hierarchy and rhythm through open typography, rules, annotations, columns or natural editorial copy rather than reproducing the carrier. This is a form constraint, not a text-minimization rule. Ordinary headings, explanatory paragraphs, specifications, ingredients, evidence, usage instructions and other visible copy remain required when the reference carries them.

The page's information density, typography, hierarchy, material, light, composition, reading rhythm and premium feel must be inherited from the observed benchmark mechanisms. Do not impose a universal minimalist aesthetic, a fixed amount of whitespace, a fixed icon count, a fixed focal-point rule or a shorter copy style. Preserve the reference's comparable text-block count, visible character volume, evidence detail and reading order, then translate only the confirmed product facts. Every visual audit must mark `decorative_label_free` true for the banned carriers while separately checking reference-bound density.

Content fidelity is also checked as a complete reference set. Before planning, classify each physical panel's function, especially the opening panel: `opening_function` must distinguish `result_promise` (hero/result promise), `problem_awareness`, `product_first`, `proof_first` and `other`. If the reference opens with a result promise, the target detail page must preserve that opening function; it may begin with pain only when the reference itself begins with pain. Then decompose the method of persuasion: `conversion_stage`, `buyer_question`, `persuasion_job`, `belief_shift`, `evidence_obligations`, and `expression_actions`. Record the semantic handoff and next buyer question at every adjacent boundary. For `DETAIL_PAGE`, the target plan must bind each screen to the observed `source_content_step_ids`, density sample and source transition, while the generation prompt derives the screen's expansion logic from those reference steps. It must not require or invent a second fixed sequence of target-side buyer questions, answers, duties or evidence objects; product facts only translate the mapped source method safely. Other routes may retain their route-specific target contracts. A title, product still life or certificate pile alone is not a valid content translation. Source wording is studied and transformed; it is never copied into the product output.

## Mixed upload role identification

When callers use repeated `--input` flags without explicit `--product-input` and `--reference-input`, every file is registered as `unassigned` with a SHA-256 hash. The workflow never uses upload order, filename, or “first image” assumptions. Before route classification, inspect each image and submit `inspect-inputs --role-review role-review.json`. The review must contain exactly one row per registered ID, the matching hash, `product_original` or `benchmark_reference`, confidence at least 0.8, a visual observation, and evidence. At least one product and one benchmark are required; missing, ambiguous, duplicated, or hash-mismatched rows fail closed. Explicit product/reference flags remain the deterministic bypass because the caller has already declared the roles.

## Routes and default contracts

- `DETAIL_PAGE`: default 10 screens in five two-screen groups (`G01` … `G05`). Each group is one continuous native Pair master at width:height `3:8` and exports two equal slices at the same native width. Explicit user counts 8–12 are accepted with evidence.
- `CAROUSEL_SET`: when the user gives no count, the output count follows the ordered source content sequence after reference analysis (minimum three); when the user gives a count, that count wins and the plan must cover all source content steps through explicit mappings. Every card has a distinct buyer question and adjacent handoff.
- `SEARCH_COVER`, `SELLING_POINT_CARD`, `PROMO_POSTER`, `BANNER`/`ECOMMERCE_BANNER`, `SOCIAL_COMMERCE_CARD`: one typed unit with its own geometry and safe zones.
- `MARKETING_VISUAL_SET`: at least three ordered format units sharing a campaign passport but retaining format-specific geometry.

For a carousel benchmark set with multiple reference images, every confirmed benchmark image must appear in the decomposition, each with an explicit `source_order`. The target units must declare `source_reference_ids` and `narrative_role` in the same sequence, so the reference set's question → evidence → takeaway logic is preserved while the product world and copy are rebuilt.

Before reference analysis, read [references/reference-analysis.md](references/reference-analysis.md). Use `init-reference-analysis` for exact eight-mechanism keys, inspect all references, supply ordered panel bounding boxes, and run `crop-reference-panels`. Each page needs its own real full-panel crop, source ID, bbox and SHA-256. Read each crop and bind its transcribed text/content step by `source_panel_ids`; then analyze the complete story. Full-panel images are analysis-only; sanitized style crops are separately registered generation evidence. Multiple panel IDs cannot reuse the same two crop files. This applies to every route.

For `DETAIL_PAGE`, read [references/detail-pair-execution.md](references/detail-pair-execution.md) before planning or generation. Every default generation is one continuous vertical Pair master with width:height `3:8`, containing only the two screens in `generation_scope` and `current_target_plan`. The request must carry `target_reference_sequence` and bind only the current Pair's ordered sanitized benchmark crops; every mapped source step in the Pair must be covered, and non-fixture crops must explicitly bind their source steps and confirm that no benchmark product pixels remain. Generic mechanism crops cannot drive a target Pair, and a missing source-step binding blocks the handoff. Never attach the whole reference set without the current source-step mapping. The prompt must show that single-screen source sequence and its handoffs so the Pair follows the assembled page order. Use the adapter-produced `imagegen-prompt-Gxx.txt` and handoff record through `scripts/artflow_render.py`; never invent a replacement prompt. Five masters are split into ten independent screens only at delivery.

Detail masters must be returned as unchanged native files at width:height `3:8` (or the 3:4 single-tail geometry for an odd explicit count). Before recording, run `scripts/render_provenance.py` against the original path returned by the artflow provider and the unchanged artwork copy. It creates a local file-comparison receipt, not a provider-signed claim. The detail record command requires this receipt. G01 and later Pair groups keep the 3:8 ratio. Changed bytes or missing original files fail closed. The artflow provider accepts an explicit canvas and quality: the requested 3:8 ratio is sent as the generation size and re-verified against the returned native file, whose true pixel size and SHA-256 are written into the sidecar receipt. A returned canvas may differ from the request by a few pixels; the aspect ratio, not the exact pixel count, is the enforced contract. Keep text and imagery in one native model pass without overlays. If the provider returns a materially smaller file, report the unsatisfied clarity contract and do not enlarge it to pass.

On a normal validation error, read the specific missing fields, correct the analysis, and rerun that command. Do not run later steps until it succeeds, modify state JSON to bypass checks, or switch a detail run to single-screen generation. `resume` reports the actual next action and preserves a pending generation request across interruption; external model capacity failures do not change the route contract.

If the process, network call, or conversation is interrupted while artwork is pending, run `resume --workspace <run>` first. When it reports `resume-imagegen-request`, run that command and use its `IMAGEGEN_RESUME_READY` handoff with the original artflow provider call. It reuses the original run ID, target, token, request, prompt and hashed product/reference bindings. Never run `generate-*` or compose a new prompt for an awaiting target. If the request, prompt, recovery manifest or any bound input is missing or changed, resume fails closed and the source file must be restored before continuing.

Read only the relevant protocol in `references/` for the selected route. `schemas/` are machine contracts; `scripts/workflow.py` is the authoritative state and gate implementation. The recovery manifest is a local continuity lock, not a provider receipt.

## Product-driven translation

Persist all six fields before generation: `reference_style_mechanisms`, `product_visual_facts`, `product_palette_contract`, `semantic_translation_map`, `route_specific_composition_plan`, and `forbidden_literal_inheritance`. Every source semantic must map to a product-native target world with confirmed fact references. Source environment, brand, packaging, logo, SKU, copy, competitor identity and literal composition are prohibited unless a documented, fact-supported retention is explicitly reviewed; the default is rejection. The same prohibition applies to newly hallucinated product names, logos and wordmarks on the target product.

Product facts do not replace reference analysis. Translate the reference's question → proof/signal → expression → next question method first, then select only the current product facts that can honestly satisfy each obligation. In `plan-target-page`, every `semantic_translation` must be an object keyed by the exact source-semantic IDs (for example `M1`), and each value must be an object containing at least `target_world` and `retain_literal_source:false`; do not flatten it into sibling `product_facts` or `translation` fields. Quantified evidence must retain its source, scope, method, sample and limitation; certificates, patents and origin documents must be labeled by what they prove and must never be used as generic efficacy proof.

For detail pages, the benchmark controls what appears, how much appears and in what order. Product facts are optional translation sources. A patent, certificate, test report or other evidence fact is used only when the mapped benchmark step actually calls for that kind of proof or explanation; its presence in the uploads never creates a mandatory screen, an exactly-once coverage task or a new selling-point order. If a fact is selected, keep its scope and source clear. Repeated source steps may repeat a fact or visible treatment when the benchmark repeats it; do not invent a difference merely to satisfy a planning rule. The last screen's internal takeaway/closing duty is planning metadata only and must never be printed as words such as “收束”, “总结” or “结尾”.

When product uploads include patents, certificates, test reports or other product-information images, inspect them and register any confirmed fact rows as available optional sources before planning; do not rely only on the user's prose summary, and do not require every registered fact to appear. If the benchmark repeats a source step or a visible treatment, preserve that repetition; if it differentiates the steps, carry over the observed differentiation. The plan must follow the reference's actual expansion logic rather than a skill-defined screen-by-screen selling-point sequence.

When more than one product-original input is registered, the facts file must also contain `evidence_inventory` with one row per product input (`input_id`, `kind`, `observed_fact_ids`). An inventory row may have an empty `observed_fact_ids` list when that input is not needed by the mapped benchmark step; inventory records availability and review status, not a display mandate.

## Copy compliance without overblocking

Do not turn compliance into a universal requirement for external test reports. Classify visible copy into four tiers: `ordinary` (normal material, color, texture, size, capacity, appearance, usage and other directly confirmed descriptions; allowed without extra evidence), `evidence_required` (patent, certification, inspection, quantified result, ranking, comparison, strong safety/efficacy or environmental claim; bind a fact and its evidence), `category_review` (medical, disease, whitening, acne, hair-loss, weight-loss and similar category-sensitive wording; review against the product category and target platform without a universal automatic ban), and `blocked` (clearly prohibited guarantees, false authority, illegal or deceptive expressions; never render). A word such as “安全” or “美白” must not be rejected solely because it can be risky in another category; judge the complete sentence, category and evidence. Normal copy remains necessary for the reference-matched information density. The generation request carries this four-tier policy and plan validation only rejects blocked copy or evidence-gated copy without a bound fact.

## Audits and provenance

Audit style inheritance, product identity, product colors/material/shape, fact and claim safety, typography, density, route safe zones, and originality. Any contamination fails. `fixture` audits prove deterministic structure only; `manual`, `visual_model`, and `real_imagegen` are separate evidence classes. Do not describe fixture success as visual quality success.

## Per-screen density and boundary fidelity

Do not turn a reference set average into a universal card grid. Record one physical panel at a time in source_content_analysis.sequence with visual_density (text blocks, card count, visible characters, main visual share, whitespace share and notes), and record every adjacent source boundary in transitions. Each target unit cites one density sample, lists exact visible_copy, and carries a density contract; adjacent target units cite the matching source transition or explain an explicit expansion/merge. The generation prompt includes these budgets and treatments as hard upper bounds: extra copy blocks, detail images, parameter rows or secondary focal objects cannot be added merely to fill the Pair canvas. Real visual audits submit density_checks and transition_checks; fixture audits remain structural evidence only. Missing or flattened rhythm fails closed.

## Image provider

This Skill renders through the local artflow provider instead of a host built-in
image tool. `scripts/artflow_render.py` is the only sanctioned bridge: it applies
the identical generation gate as `scripts/imagegen_adapter.py`, writes the bounded
`imagegen-prompt-<key>.txt`, and then renders.

```powershell
python scripts/artflow_render.py --workspace <run> --request <run>/artifacts/generation-request-Gxx.json --output <run>/actual-pair-Gxx.png
```

- Credentials are read from the first existing of
  `%USERPROFILE%\.workbuddy\artflow-config.json`,
  `%USERPROFILE%\.workbuddy-ai\artflow-config.json`,
  `%USERPROFILE%\.codebuddy\artflow-config.json`,
  `%USERPROFILE%\.artflow\config.json` -- or from `$WORKBUDDY_CONFIG_DIR` /
  `$CODEBUDDY_CONFIG_DIR` when the host application sets them. Keys:
  `api_key`, `base_url`, `model`, `quality`, `timeout`, `retries`,
  `max_references`. Override per call with `--api-key` or the `ARTFLOW_API_KEY`
  environment variable, and with `--model` / `--quality`.
- Available models: `gpt-image-2.5-flare` (default) and `gpt-image-2.5-sunburst`.
- The provider sends the request's declared canvas as the generation size.
  Verified native canvases: 3:8 -> 1440x3840, 1:1 -> 1536x1536, 2:3 -> 1024x1536.
  A returned canvas may differ from the request by a few pixels; the aspect ratio,
  not the exact pixel count, is the enforced contract.
- The file written to `--out` is the provider's native original, byte for byte.
  Nothing is resized, resampled, upscaled or overlaid locally.
- Every render writes a sidecar `<out>.receipt.json` holding the true pixel size,
  SHA-256, requested canvas and model. Read it before recording artwork.
- `--dry-run` resolves the prompt, references, canvas and quality without spending
  a generation. `--list-models` verifies credentials and connectivity.
- Without `--request`, ad-hoc use is `--prompt` or `--prompt-file` plus repeatable
  `--reference <path>` and `--size WxH`.
- A request that carries no generation token or packet id is rejected unless
  `--allow-untokened` is passed, so a hand-written prompt cannot silently bypass
  the workflow's own gate.

### Rendering a set of images

Rendering is network-bound: measured across this project's history the provider took a
median of ~53s and up to 239s per image, so issuing them one at a time costs the whole
sum. When the plan gives you a group of renders that do **not** depend on each other's
results - a full set of masters, one pass of variants, the SKU packets - hand them to
the batch renderer rather than looping over the single-image command:

```bash
python scripts/artflow_generate_batch.py \
  --job "<request-a.json>=<image-a.png>" \
  --job "<request-b.json>=<image-b.png>" \
  --job "<request-c.json>=<image-c.png>"
```

This is the same provider call per image, honouring each request's own single-use
generation token and writing the same `<out>.png.receipt.json` sidecar per image - the
only difference is how many are in flight.

**It picks the concurrency itself, and you should let it.** Whether overlap helps is the
provider's property, not ours, and it was measured on this gateway rather than assumed:

| render type | simultaneous | measured |
| --- | --- | --- |
| prompt-only (`images/generations`) | 4 | four good images; 698s of provider time in 176s of wall clock (**3.96x**) |
| carries references (`images/edits`) | 2 | **two HTTP 413s** - both jobs lost |
| carries references (`images/edits`) | 3 | two HTTP 413s, one image |
| carries references (`images/edits`) | 1 | three for three, each good |

The gateway draws the line at carry-along reference images, and almost every render in
this Skill family carries one. So the default already matches the measurement: prompt-only
renders fan out to 4, reference-bearing renders go one at a time. Do not override
`--edits-concurrency` upward to make it faster - it has been tried, and what comes back is
not a slower render but no render. If you genuinely know your gateway accepts overlapping
edits, that is the flag to change, with evidence.

Two rules that are not negotiable:

1. **Name every output on the command line**, as above. Never move the output paths into a
   JSON file: the skill audit attributes each image to the call that produced it by looking
   for its name in the recorded command, and a path that only ever appears inside a file is
   read as having no origin - the image is then refused exactly as a hand-made one would be.
2. **A non-zero exit means the set is not finished.** Anything that failed is retried one at
   a time before the command returns; whatever still fails is handled exactly like a failed
   single render. Keep dependent renders out of one batch - if image B must be judged before
   request C exists, C cannot be in it.


---

<!-- IDENTITY-CONSISTENCY-LOCK:BEGIN（由 skill `ecom-product-identity-consistency` 追加，可整块删除） -->

## 产品一致性硬锁（不可跳过）

本 skill 的审计普遍只覆盖**污染项与文案**，**不覆盖产品形态保真**（画出来的商品是否等于实拍商品）。因此出图前后必须额外执行以下六条，细则见 skill `ecom-product-identity-consistency`：

1. **出图前写几何**：读真实产品照片，量出截面形状、宽高比、结构分段、局部尺寸比，写进身份摘要；**几何条款不得少于 3 条**。只写功效 claim 不带几何 = 模型必然回退到品类通用款（这是产品一致性出错的头号原因）。
2. **做视觉锚**：用 `ecom-product-identity-consistency/scripts/make_boards.py` 生成产品形态板；局部特写屏另生成部件放大图。裁切只留产品本体，去掉包装盒/文案卡/桌面杂物。
3. **参考图按决策树**：修形态时**禁止**把任何已生成成图放进参考位（会被原样复制）；无产品页（成分/技术说明页）**不传任何参考图**；参考顺序＝真实身份图在前、风格裁切图在后。
4. **成分配图写物质不写物**：成分名按物质形态表达（微粒/晶体/膏体/液滴/膜），**禁止实物道具**（不画果实、植物、食材）；禁止卡通与扁平图标；多卡嵌图规格必须统一。映射表见该 skill `references/ingredient-material-map.md`。
5. **出图后逐屏目检六项**：截面 / 比例 / 结构分段 / 局部层数 / 标签拼写逐字母 / 大数字完整性；形态项必须与实拍并排比对，不通过不得进入下一屏。
6. **返工策略**：同一屏 3 轮不过必须换策略（换参考图粒度或换取景尺度），不要继续微调文字；失败版本留档到 `artifacts/discarded/` 并带 prompt 版本号。

可直接粘贴的 prompt 段落模板见该 skill `references/hard-lock-template.md`。

> **状态：已启用硬门禁（B 层）** 本 skill 已启用硬门禁（B 层）：`workflow.py` 的 `facts()` 强制产品事实集至少一条 fact 用具体几何描述产品形态（截面/宽高比/结构分段），只有功效文案（如除臭率/成分百分比）而无几何会被 BLOCKED。出图后 `audit()` 的 `dimensions` 已强制 `product_material_shape` 维度为 True。

<!-- IDENTITY-CONSISTENCY-LOCK:END -->
