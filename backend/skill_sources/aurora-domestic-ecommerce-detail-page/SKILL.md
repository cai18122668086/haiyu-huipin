---
name: aurora-domestic-ecommerce-detail-page
description: Plan, generate, inspect, repair, assemble, and deliver fact-grounded domestic Chinese e-commerce detail pages with a product-driven 8, 10, or 12-screen storyboard rendered in mandatory adjacent two-screen 3:8 continuous pairs and deterministically decomposed into single-screen deliveries. Use for creating or revising 淘宝、天猫、京东、拼多多等国内电商详情页、详情长图、商品卖点长页、分组策划、逐组生图、切图或详情页验收. Also use when resuming after user feedback or interruption; the workflow rehydrates product identity, approved story, series visual DNA, and current-pair constraints before continuing.
---

# 极光国内电商详情图（v4.3.1）

## Optional website contract v1

For website requests explicitly selecting `website_contract_version=WEBSITE_LEAN_V1`,
read [website-lean-v1.md](references/website-lean-v1.md). This versioned profile uses
one authored primary claim/job map and six core pixel checks per fixed slice. It
replaces the repetitive aesthetic measurement checklist for those approved website
plans only. Facts, identity, source boundaries, selling-point depth, pair geometry,
actual pixels, original receipts and the one-repair limit remain mandatory. Existing
plans and ordinary standalone requests retain their original contract.

## v4.3.1 internal seam correction

An atomic screen's `transition_contract` describes the incoming handoff from its
previous screen to itself. For an adjacent pair `(upper, lower)`, the internal
seam therefore uses the **lower screen's incoming transition**, from the upper
screen's bottom region to the lower screen's top region. Validation, approval,
generation requests, prompt compilation and pixel audit must bind that same
contract. Explicit screen references outside that pair are rejected. The upper
screen's own incoming handoff belongs to the previous pair and must not replace
the current pair's internal seam.

This patch preserves the v4.3 story, product, fact, geometry, repair and audit
gates. Existing v4.3.0 sessions remain historical state; do not rewrite or
automatically reauthorize them under this patch.

## Immutable accepted audit evidence

Every validated pair audit now seals its exact input JSON bytes and native decision in an immutable per-render-token file under `audit-originals`. Both slice attempts bind the same snapshot, render token, cycle, signed request and exact pixel lineage. The read-only `inspect-pair-review-evidence --session <session> --pair-id <pair>` verifies the accepted pair against that original snapshot and revalidates its atomic report bindings. It must reject missing/changed snapshots, changed accepted observations, sibling disagreement or references to another attempt/pair. A later repair keeps the first audit intact and uses a different snapshot for its own signed token.

Website recovery additionally requires every cited immutable provider-review request, response and original UTF-8 model message. Reconstruct the accepted observation from the original pair report and its optional one same-pixel identity follow-up, preserve explicit false/applicability, and compare it to the sealed audit input. A prior file-complete canvas never substitutes for these receipts. Compare typed canonical JSON fingerprints; a boolean true must not equal an integer 1 in accepted observations or receipt copies. Missing or uncertain evidence stops reuse without a new provider call. Explicit offline synthetic fixtures remain distinguishable from actual provider/pixel quality acceptance. Historical tasks keep their original pinned runtime and are not silently rewritten to acquire this evidence.

## Truthful identity observation scope

The canonical `schemas/identity-observation.schema.json` distinguishes actual full product, identity token and no visible product. Derive scope from each slice's observed presence, never from planned permission: `VISIBLE_COMPLETE_PRODUCT` requires three boolean comparisons and concrete visible features; `VISIBLE_IDENTITY_TOKEN` compares only visible features and marks unobservable comparisons `NOT_APPLICABLE`; `NO_VISIBLE_PRODUCT` requires three `NOT_APPLICABLE` values and no claimed visible product features. Unknown visibility/comparisons remain incomplete and stop processing. Aggregate pair scope is the strongest actual scope of its two slice reports. Explicit false observations survive bounded identity follow-up. Unexpected full product or unexpected absence is still a role/presence failure, with the existing single whole-pair repair budget and preserved residuals. See [product-presence-rhythm.md](references/product-presence-rhythm.md).

## v4.3 expression diversity and anti-posterization

v4.3 keeps the v4.2 state machine and `information_contract_version=4.1`, and adds a separate `expression_contract_version=4.3`. A plan must now declare the first visual subject, presentation form, complete-product role, expression signature, visual cast and structured content units for every screen. Campaign-level diversity budgets are checked before approval and again from observed pixels after generation, so a correct plan cannot silently collapse into ten packshot posters.

Prominent named formula, ingredient, structure and element facts are now machine-routed before planning. Every eligible fact must be explicitly evaluated as a dedicated screen, a controlled merge, or a justified non-trigger; enabled contracts may no longer omit such facts. Ingredient screens must use a truthful macro, source/path or process carrier, visibly name the ingredient when authorized, and keep conceptual visualization distinguishable from microscopy, testing or molecular evidence.

v4.2 sessions are read-only historical state under this runtime. For unfinished work, follow [v4.3-migration.md](references/v4.3-migration.md) and recompile from raw evidence; never patch an old session in place.

## v4.3 white-label (BRANDLESS) support

When the user explicitly confirms that a product carries no usable brand mark, lock `brand_logo_source.mode = BRANDLESS` with a recorded `brandless_evidence` string, `image_id: "NONE"`, `authorization_basis: "NOT_APPLICABLE"`, `readability: "NOT_APPLICABLE"` and `usage: "FORBIDDEN_ALL_SCREENS"`. The campaign then runs with zero independent logos on every screen: the first screen is anchored by the real product subject, truthful category/specification lettering and the campaign visual DNA instead of a logo. No screen may invent, approximate, translate or imitate a brand name or mark. `BRANDED` campaigns are unchanged, and a missing readable mark without an explicit white-label confirmation still stops planning. The mode is enforced at identity lock, at plan compilation and at the per-screen pixel gate.

## v4.2 integrity hardening

v4.2 makes JSON Schema the first validation layer, preflights every compiled module/pair prompt before approval, signs every generation request, binds pixel judgments to image SHA-256 and audit provenance, adds controlled same-request candidate recovery, and supports explicit `AUTO_FINALIZE_AUTHORIZED` delivery. High-risk cosmetic facts now require a machine-auditable claim-risk contract. The information model remains `information_contract_version=4.1`.

v4.1 sessions are also read-only historical state. The v4.2 migration notes remain a historical description of the preceding upgrade.

## v4.1 详情页信息架构补强

v4.1 在事实、Pair、身份和像素合同之上增加三项运行时约束：M01 必须声明唯一商品英雄；每个模块必须回答明确买家问题并声明新增购买理解；每个 Pair 必须通过信息增量、购买判断和非海报化审计。所有新计划统一使用 `information_contract_version=4.1`、`planning_mode=LIGHTWEIGHT_V41` 和 `plan_format=COMPACT_V1_3`。

Build one coherent product-detail campaign from verified product truth. Let the Agent decide commercial story, visual proof, composition, and aesthetics. Use the harness only to preserve facts, product identity, continuity, state order, and repair limits.

v4.1 keeps continuous-pair rendering as the default campaign mode, derives exactly 8, 10, or 12 screens directly from selling-point depth, and adds a deterministic plan preflight that batches structural and mapping errors before strict compilation. The planning conversation completes all atomic screen plans first, groups adjacent screens into ordered pairs second, and submits one hierarchical group→screen packet for approval. There is no separate single-screen ratio planning or approval step: 3:8 is the sole user-facing group-canvas ratio, while the single-screen window is internal fixed decomposition geometry. Pair-to-pair continuity is deferred to optional assembly/audit. Generation uses one 3:8 canvas per pair; the canvas is then decomposed deterministically into two single-screen delivery files.

## v4.1 default planning order: single-screen storyboard → ordered grouping → group—screen approval

Use this order on every new or resumed campaign unless the user explicitly requests another sequence:

1. Complete the fact-grounded product-driven atomic storyboard and expose it for review. Choose an even screen count supported by the product's selling-point depth; do not default to 12 unless the product needs it.
2. Group the completed screens mechanically into adjacent ordered two-screen pairs: `(M01,M02)`, `(M03,M04)`, `(M05,M06)` and so on. Grouping must preserve atomic storyboard order exactly; it may not reorder, skip, overlap or cross-pair modules. State clearly that grouping changes the raster container only; it never merges screen duties, claims, copy whitelists or product-presence decisions.
3. For each pair, plan the internal handoff between its upper and lower screen. Specify one primary method, source region, seam action, target region, direction/scale/light/material continuity, allowed subject-state change and method-specific evidence requirement.
4. Present the atomic screen plans and the pair map/internal seam contracts together as one hierarchical `group → screen` approval packet. In the approval narrative, each group is the top level, with its two single-screen plans and their internal seam nested beneath it. Do not create a separate user approval checkpoint for the pair map or for individual screens.
5. Only after the user approves the complete packet may the pair compiler produce the 3:8 generation requests. Pair-to-pair transitions remain deferred to optional long-page assembly/audit.

The user's attached method examples are reference grammar, not execution instructions. Map them to the internal method IDs when useful—object-edge transition to `EDGE_BLEED`, product crossing to `PRODUCT_OVERFLOW`, shared background to `BACKGROUND_STRINGING`, perspective turn to `PERSPECTIVE_TURN`, decorative transition to `DECORATIVE_HANDOFF`, graphic overlay to `GRAPHIC_OVERLAY`, state change to `SUBJECT_STATE_HANDOFF`, and chapter transition to `CHAPTER_BAND`. Do not copy their brands, wording, numbers, watermarks or layout skeletons.

## Mandatory reading

Read these files before the named stage:

- Before intake or identity locking: [core-policy.md](references/core-policy.md) and [intake-and-evidence.md](references/intake-and-evidence.md).
- Before deciding how to handle supplied or missing selling points: [product-operation-routing.md](references/product-operation-routing.md).
- Before planning (lightweight compact default): read only [detail-story-grammar.md](references/detail-story-grammar.md), [visual-proof.md](references/visual-proof.md), [storyboard-matrix.md](references/storyboard-matrix.md), [continuity-and-rhythm.md](references/continuity-and-rhythm.md), [compliance-and-platform.md](references/compliance-and-platform.md), and [reference-intelligence.md](references/reference-intelligence.md). Read deeper routing, typography, aversion, performance, archetype and sales-depth references only when the product or selected screen explicitly needs them.
- Before generation, repair, recovery, audit, or user correction: [series-visual-dna.md](references/series-visual-dna.md), [continuity-and-rhythm.md](references/continuity-and-rhythm.md), [continuity-and-variation-audit.md](references/continuity-and-variation-audit.md), [retry-and-feedback-control.md](references/retry-and-feedback-control.md), and [generation-and-audit-integrity.md](references/generation-and-audit-integrity.md).
- Before planning a high-risk cosmetic assertion: [high-risk-cosmetic-claims.md](references/high-risk-cosmetic-claims.md).
- For sequential screen-to-screen handoff: [cross-module-continuity.md](references/cross-module-continuity.md).
- Before any compliance or export decision: [compliance-and-platform.md](references/compliance-and-platform.md).
- Before judging pixels or assembling the long page: [proportional-audit.md](references/proportional-audit.md).

## Authority and boundaries

Apply this authority order:

`USER_FACT > DOCUMENTED_EVIDENCE > OBSERVED_PRODUCT_EVIDENCE > REFERENCE_GRAMMAR > ENGINEERED_DEFAULT > MODEL_FREEDOM`

- Never invent or strengthen a price, effect, test, certification, structure, ingredient, quantity, accessory, service, comparison, or platform rule.
- Treat reference images as visual grammar only. Never copy their brand, product, copy, numbers, certification, proprietary layout skeleton, or source watermark.
- Preserve the current product's shape, proportions, color, packaging, labels, parts, quantity, and physical relationships.
- Reject source-background leakage unless the user explicitly requests that environment.
- Treat the user's supplied selling-point set as standing coverage authorization. Account for every fact and retain every attributable, purchase-relevant point by default; merge related points or add modules instead of silently deleting them.
- Use a zero-question default. Ask only when a real SKU conflict or a material misleading/legal risk cannot be resolved from supplied evidence, and batch all such conflicts into one question.
- Do not over-delete useful supplied formula, ingredient, texture, process, scenario, specification or use information merely because it needs qualification or has weak photographic potential. Change wording and proof form before dropping relevant content.
- Use a product-driven screen count of exactly 8, 10, or 12 for continuous-pair campaigns. Determine the exact count from selling-point depth and buyer doubt; do not default to 12 when a shorter approved story is sufficient.
- Do not inherit an aspect ratio from a prior run or template. Decide one campaign ratio from target platform, mobile reading, product geometry, visual-proof needs, content density, generation reliability, and delivery slicing.
- Treat module count and aspect ratio as approved campaign decisions. Do not generate until both are exposed to and approved by the user.
- One module may carry several related points, but must have one clear lead conclusion.
- Never interpret successful file creation, schema validation, or image-generation receipt as visual success. Inspect actual pixels.

## Native final delivery evidence

The finalize and auto-finalize commands share one native manifest projection.
Accepted module order, exact file hashes, original pixel audit bindings, review
snapshots, accepted pair-canvas hashes and material residuals remain explicit.
An optional long-page audit is preserved when present; its absence never becomes
a passing campaign-audit claim. Finalization records the exact manifest byte
hash in the native session.

The inspect-final-manifest --session <session> --manifest <exact-output> command
is read-only. It checks the finalized state, recorded output path and byte hash,
then reconstructs the expected manifest from the same accepted native truth.
Missing or changed output, altered residuals, unfinished modules and unapproved
automatic delivery are rejected. Website delivery must additionally verify its
original generation/review evidence and durable output bytes before accepting
the user's final review. Historical finalized sessions lacking this evidence
keep their original pinned runtime.

## v4.3 non-negotiable gates

These gates override any shortcut, prior conversational habit, or ad-hoc image prompt:

1. **Conflict before plan.** Complete a source-conflict scan before writing a full storyboard. A material conflict means two supplied claims cannot both truthfully describe the same SKU, specification, formula, audience, performance result, included item, certification, or use condition. When one exists, list every conflicting value and its source in one batched user question, stop, and wait. Do not show a full plan, choose screen duties, or lock identity until the user confirms which facts survive. Do not ask about harmless wording differences or facts that can coexist.
2. **Selling-point depth determines screen count directly.** First cluster all active facts by buyer question and purchase role, then distinguish “mentioned” from “sold through”. Derive one even screen count from the required decision jobs, independent proof depth, evidence boundaries and visual rhythm. Do not generate or present alternative count candidates or compact/balanced/full-sales trade-off analysis. Record one concise rationale explaining why the chosen count is sufficient and why adding or removing a pair would weaken the story.
3. **Visual first, copy assisted, detail-page informative.** “少文字” means the image carries the proof while concise copy adds purchase-relevant understanding that a main image cannot hold. It never means “delete all useful copy.” A normal detail screen should usually carry 3–5 concise information units: one lead conclusion, one short explanation of why it matters or how it works in use, and one or two object-/parameter-/condition-bound labels; add a fifth qualifier only when it changes selection or truthful use. Hook, emotional scene, result and closing screens may use 2–4 units when the image already carries more meaning. Only an explicitly approved breathing screen may use 0–2 units. Do not turn every unit into a separate card or repeat the same fact in different wording.
4. **Reference analysis must enter the plan.** For every supplied analyzed reference, explicitly adopt or reject it. Every adopted visual-expression method must be scheduled to named modules and adapted to this product. References may teach subject scale, scene construction, proof relationship, information hierarchy, typography placement, comparison, process, material macro, or pacing; never copy their brand, wording, proprietary asset, watermark, or whole layout.
5. **Approved plan is a rendering contract.** Generation must preserve the approved module duty, lead conclusion, selling-point cluster, proof relation, required subjects, copy whitelist, reference-expression pattern, composition family, and visual DNA. A generated screen that omits or substitutes any of these is a material failure even if it looks attractive.
6. **One repair means one repair, then continue.** Each explicit revision cycle contains at most two renders: one initial attempt and one automatic repair. If the repaired image still materially fails, never render a third time. Bind attempt two as `COMPLETE_WITH_RESIDUALS`, preserve every residual failure, disclose it in final delivery, and automatically advance to the next module without pausing for user direction. A later explicit user instruction may open a new revision cycle for the named module, with prior failures retained in history.
7. **M01 has a hard brand-anchor gate, in one of two modes.** A branded campaign (`brand_logo_source.mode = BRANDED`) must show exactly one clear independent brand logo on the first screen that visibly matches a readable user-supplied current-product source; a package-printed logo does not replace this independent first-screen brand anchor. A white-label campaign (`mode = BRANDLESS`, locked only on explicit user confirmation with `brandless_evidence`) shows zero independent brand logos on every screen and replaces the first-screen anchor with a non-logo product anchor: the real product subject, truthful category/specification lettering and the campaign visual DNA. In both modes M02 onward show zero independent logos, package printing remains natural product identity, and no module may invent, mimic or approximate a brand name or mark.
8. **Typography is planned, not improvised.** Lock a campaign-wide typography rhythm and a per-module typography contract before approval. Every module declares text mode, anchor object, anchor zone, reading direction, hierarchy, line budget, container treatment, reserved negative space, image/text relationship, reflow permission, and visible difference from adjacent modules. “少文字” never authorizes a generic centered title, a repeated top-left poster, or a weak image with one slogan.
9. **Pain-to-solution is a story obligation.** Every normal sales detail page must schedule a dedicated pain screen or a combined pain-solution screen. The pain must be a visible affected object/person/environment, undesired state/action and consequence; a title, word cloud, icon row or generic worried face is not coverage. Only an explicitly user-requested non-sales information page may claim exemption. Pain context may be inferred cautiously, but the product solution must bind verified value bridges.
10. **Visualizable means visually carried.** Every module must declare a visual-expression mode and primary semantic carrier. If the selling point can be shown, its meaning must remain at least partially understandable with promotional copy hidden. Text and icons may name, qualify or quantify visible evidence; they may not replace it. Reject static packshot-plus-copy, icon-only proof and generic product-swap screens. Across the page, vary carriers and allow pain/action/process/result scenes to outrank the packshot while keeping a clear visual return to the product benefit.
11. **Primary selling points receive sales depth.** Split any proposition that could independently change a purchase decision into its own atomic claim. Every must-remember claim is `PRIMARY` and `DEEP`: it needs overview/context, a module where it is the lead claim, at least one different use/result/mechanism/selection/trust job, and closing recall across at least three modules. A supporting appearance never counts as dedicated depth. When several primary claims share one screen, that screen counts only as overview unless each claim has its own visible zone, proof relation and mobile scan stop.
12. **Spatial structure is a rendering contract.** Every module must declare observable zones, their geometry and weight, the structural relationship, full-bleed permission, panel/mini-scene counts and a visible difference from its neighbor. A new subject inside the same full-bleed poster is not variation. Across a normal campaign, never plan two consecutive full-bleed single-scene modules. The first-screen style master may transfer palette, light, finish, typography character, graphics and product rendering, but never its layout skeleton, coordinates, occupancy, text zone, camera scale, zone geometry or full-bleed template.
13. **User selling points are ledgered, never silently lost.** Under `USER_SELLING_POINT_LED`, record every original user point once as `ACTIVE`, `MERGED`, `QUALIFIED`, `DEFERRED`, or `CONFLICT_BLOCKED`, with atomic claim IDs, planned modules and a reason. Under `PHOTO_TO_PRODUCT_STRATEGY`, infer buyer, pain and positioning, but keep hypotheses separate from verified facts.
14. **Screen count is derived after depth.** Calculate `base narrative jobs + primary depth expansion + supporting decision jobs - genuine shared-proof savings`. Shared-proof savings require the same buyer question, proof relation and commercial conclusion; similarity of wording is not enough. Resolve one count from 8, 10, or 12 and record only that count and its rationale.
15. **Internal evidence language never becomes sales copy.** Packaging location, evidence source, missing reports, verification state, risk workflow, “以包装标示为准”, “购买前请核对” and equivalent phrases remain machine-side. If a legal condition must be displayed, write only the minimum actual condition as a subordinate qualifier; never expose the internal reason for qualifying it.
16. **Audit evidence must describe pixels.** Every module and long-page check names distinct visible objects, locations, relations or text actually observed. Repeated “actual pixels match contract” language, prompt-based assertions and empty check templates are invalid. The long-page audit must separately verify overview, dedicated proof, use/result and recall for every primary claim.
17. **Pain expression is category-neutral and routed, never templated.** A pain module must classify the pain subject, relation, visibility, multiplicity, consequence and emotional intensity, then choose a fitting expression form and spatial structure. The same form may serve any category when the pain structure matches. Do not default every pain screen to one full-bleed lifestyle photograph, one worried person, a four-icon row or a health-category symptom board. Across campaigns, route among immersive moments, macro hotspots, multi-pain arrays, trigger-state-consequence chains, behavioral-consequence proxies, context spectra, object/environment friction, conceptual character stories, documentary sequences, controlled contrasts and asymmetric editorial collages. Examples teach expression grammar only; they never create category facts or claims.
18. **Pain must be recognizable without becoming repellent.** Every pain, hygiene, contamination, damage, biological-object or failure-state screen must set a commercial-comfort target. Show enough evidence for instant recognition, but sanitize scale, crop, texture, wetness, color and canvas dominance so disgust, fear or bodily discomfort does not become the strongest memory. High-aversion subjects such as bodily fluids, oral interiors, lesions, decay, parasites, excrement, severe mould, rotting material or dense contamination may not dominate the canvas in photoreal extreme macro. Use a localized hotspot, distanced context, restrained crop, clean symbolic substitute or clearly illustrative diagram while preserving the truthful pain relation.
19. **Product presence is a campaign rhythm, not a per-screen quota.** Every new compact module declares `product_presence_decision`. Use a complete product only when it performs a unique buyer job: establishing identity, participating in an action, enabling selection/specification, restoring recognition at a meaningful peak, or closing purchase. Source, material, mechanism, dedicated proof, result, lifestyle and trust screens must actively consider `ABSENT_PREFERRED`; a corner packshot, blurred background package or decorative mini product still counts as product presence. When the complete product is absent, bind the screen to this product through verified material, source, action, result, approved detail, palette, graphic language or other product-specific evidence. The first and closing screens still return to the complete product.
20. **Expression diversity is contracted and pixel-audited.** Every screen declares one primary subject class, one presentation form, one complete-product role, one visual cast and one expression signature. For 8/10/12 screens, plan at least 4/5/6 primary-subject classes and 5/6/7 presentation forms, while limiting dominant complete-product screens to 3/3/4. Adjacent screens may not share the same expression signature. The long-page audit recomputes these values from actual pixels; planned diversity alone is not a pass.
21. **Information density is made of visible content units.** A normal proof screen carries 3–5 distinct content units, while hooks, use/result and closures carry 2–4 and breathing screens 0–2. Units must map to visible copy, a verified fact and a visible anchor; repeating one slogan in several forms does not increase density. Approval packets expose these units before generation.
22. **Named formula and ingredient facts are deterministically routed.** Every verified `INGREDIENT_FORMULA` or `STRUCTURE_COMPONENT` fact is evaluated exactly once. A user-prominent, `MUST_SHOW`, named fact must receive `DEDICATED`, or a narrowly justified `MERGE_ALLOWED` route with the same buyer question, benefit, evidence ceiling and visual route. Formula modules use ingredient/material/process subjects rather than a dominant complete product and obey the conceptual-visualization evidence boundary.

## v4.1 direct-count planning, continuous-pair rendering and seam-evidence gates

Continuous-pair rendering is mandatory for every approved campaign. The storyboard, claim ledger, product identity contract, and module IDs remain per-screen; a pair is only a raster-generation container.

1. **Pair indexing is mechanical; pair transition is semantic and visual.** Group the approved sequence as `(M01,M02)`, `(M03,M04)`, `(M05,M06)`, `(M07,M08)`, `(M09,M10)` or another explicitly approved two-screen grouping. The merge step must redesign the visual handoff between the two atomic contracts: preserve each screen's duty while specifying source state, bridge carrier, boundary action, target state, camera/light/material continuity, and the resulting reading direction. Never concatenate two prompts mechanically or merge two module duties, claims, copy whitelists, or product-presence decisions into one undifferentiated brief.
2. **One pair, one AI canvas.** Request one continuous 3:8 canvas for each pair (for example 1440×3840, or the exact proportionally equivalent approved profile). The prompt must state the two fixed vertical screen windows and require the artwork to cover the whole canvas. Do not generate the two screens separately and call them a pair.
3. **Atomic screen contracts inside the pair.** The pair prompt must expose, for both screens, the module duty, lead conclusion, required visual subjects, visible-copy whitelist, composition family, product-presence decision, and forbidden mutations. The pair may share palette, light, material and spatial atmosphere, but content remains bounded to its named screen window.
3A. **Pair-plan merge is a mandatory creative compiler stage.** First compile the two atomic screen contracts independently for planning, fingerprints and audits. Then synthesize one `PAIR_PLAN_MERGE` prompt containing both contracts plus an explicit transition beat: what visual carrier leaves the upper window, how it changes inside the seam zone, what the lower window receives, and how direction, scale, light, material and atmosphere remain continuous while the composition changes. The merged prompt must be a newly authored bridge brief, not a text concatenation. Send only this merged pair prompt to the image backend; `screen_prompts` are retained as audit contracts and are never sent as separate image requests. Never carry an independent 3:4 format-canvas lock into either nested screen contract, because it makes the backend render two separated posters instead of one continuous long canvas.
4. **Internal pair seam is intentional.** Place the 1920px boundary in a declared transition zone. The boundary must use a clean background, soft light, water/foam, a decorative curve, or another approved visual carrier. Do not place a complete heading, information card, tooth macro, or duplicate product at the pair seam unless the approved seam contract explicitly requires a single object to cross it.
5. **Pair-to-pair seam uses a shared transition token.** Independent pair canvases cannot be assumed pixel-identical. Define one transition token for the campaign (for example silver-white to pale grapefruit gradient + sparse droplets + one curved line), and declare its top/bottom behavior for every pair. The token must be visually quiet, contain no claim-bearing copy, and use the same color, light direction, texture density, and edge geometry across pair boundaries. If the backend supports a fixed seed or image reference, lock it; otherwise judge visual continuity, not pixel identity.
6. **No duplicated carry-over.** Never copy a complex foreground subject, text card, or full product from one pair into the next pair's transition zone. If a product or fruit must cross a boundary, generate it once in a single pair canvas and keep the crossing inside that canvas. Across pair boundaries, prefer background or decoration-only handoff.
7. **Fixed decomposition.** After generation, decompose each pair canvas at the approved fixed window height exactly. The top and bottom delivery files must retain their configured dimensions and structure; do not resize either half independently, move the window to hide a failed seam, or crop away an approved screen contract. Record the 3:8 pair-canvas dimensions, fixed decomposition coordinates, and resulting module IDs.
8. **Per-screen audit remains mandatory.** Audit M01 and M02 (and every later pair) separately after slicing for duty, claims, copy, product identity, visual carrier, spatial structure, and product-presence rhythm. Then audit the pair seam and the four pair-to-pair seams as a separate long-page check. A visually smooth pair does not pass if either sliced screen loses its approved content.
9. **Repair scope.** A failed internal pair seam, missing screen duty, or product inconsistency requires one repair of the whole pair canvas, not independent patching of one half. A failed pair-to-pair seam is repaired by regenerating the affected pair with the shared transition token; do not create a third render in the same explicit revision cycle.
10. **Fallback rule.** If the image backend cannot reliably produce the requested long-canvas ratio or repeatedly collapses the two screen windows, stop continuous-pair mode for that pair and report the constraint. Do not silently fall back to separately generated screens or post-hoc foreground compositing while claiming continuous AI generation.

11. **Method-specific seam evidence is mandatory.** A declared `method_id` is only a plan instruction, not proof. The long-page judgment must provide `internal_seam_evidence` for every pair and use the method-specific visible relationship in [cross-module-continuity.md](references/cross-module-continuity.md#v40-方法专属接缝证据与方案合并硬闸门). `EDGE_BLEED`/`PRODUCT_OVERFLOW` require a real carrier crossing the fixed boundary; `SUBJECT_STATE_HANDOFF` requires a visible state relation; `PERSPECTIVE_TURN` requires direction plus scale/plane continuity; `BACKGROUND_STRINGING` requires a continuous shared field; `DECORATIVE_HANDOFF`/`GRAPHIC_OVERLAY` require a shared carrier touching both sides; `CHAPTER_BAND` requires an intentional, quiet shared token. Generic statements such as “画面连续” are invalid.

12. **Pair-to-pair evidence is separate.** Every independent pair boundary must be listed in `pair_transition_audits` with the approved transition token, concrete source/target regions, visible shared carrier, and hard-separator result. A missing or failed internal seam or pair-to-pair audit prevents `PASS`; only `PASS_WITH_RESIDUALS` may carry a disclosed seam residual.

## Agent and harness responsibilities

The Agent must:

- understand the exact product and buyer;
- identify dominant and supporting purchase-decision types;
- rank doubts and selling points;
- choose direct, action, scene, comparison, or text-only proof;
- design the story, rhythm, camera, scale, space, light, visual rhetoric, and concise copy;
- freeze a reusable series visual passport, then schedule different composition, camera, text and proof grammars across modules;
- schedule observable spatial structures across the full sequence and define each module's zones before approval;
- make text respond to subject position, gaze, action, evidence, contrast and negative space instead of defaulting to a centered display title;
- inspect every generated module and judge whether the image truly proves the intended value.
- always preserve the per-screen storyboard as the source of truth, compile each ordered pair as two atomic screen contracts followed by one explicit pair-plan merge, and never replace two screen contracts with one vague long-poster brief.

The harness must only:

- persist facts, identity locks, approved story, campaign constitution, module roles, attempts, and assets;
- reject illegal state transitions or stale generation tokens;
- compile one current-module prompt at a time;
- in continuous-pair mode, compile both approved atomic screen contracts, merge them into one pair-level `PAIR_PLAN_MERGE` prompt, and retain the atomic duties, copy whitelists, product-presence decisions, spatial structures, and audit IDs for both screens;
- allow exactly zero or one automatic repair per explicit module revision cycle; never a hidden third render;
- preserve unaffected modules during local user revisions;
- verify dimensions, order, file integrity, and final assembly.
- verify pair-canvas dimensions, fixed slice boundaries, pair-to-pair transition bands, and the mapping from every sliced screen back to its approved module contract.
- reject prompts that leak internal workflow metadata and reject claim-bearing pixels outside the approved visible-copy whitelist; a small, visually bound, non-claim pain-scene caption may remain advisory under the proportional audit rules;
- keep internal copy roles, anchors, bindings and planning explanations outside the pixel-facing prompt; only exact `visible_copy.text` strings may enter as promotional copy. Non-claim visual organization marks such as a consecutive `1/2/3` step sequence, arrows, bullets or separators may appear when they only organize already visible actions and add no product fact;
- keep copy density, visual payload, proof strength and commercial energy as separate controls;
- persist campaign invariants globally and local corrections only on their named module, so one screen's typography complaint cannot pollute later screens.

## Context rehydration gate

Treat continuation after user feedback, interruption, compaction, tool failure, or a later turn as a cold restart of execution context. Before planning, generation, repair, or resuming a module:

1. Run `inspect` on the active session.
2. Reload the raw user brief, product-image inventory, identity contract, approved plan, and current-module request from disk.
3. View the original product identity images again. For M02 onward, also view the accepted M01 series master and the immediately preceding accepted module.
4. State the exact canonical product, current module duty, single conclusion, product evidence, visual-master anchors, text mode, and forbidden mutations in a short internal run brief.
5. Abort and restart intake when any of those sources conflict or when the user says to forget the existing plan, first screen, or generated assets.

Never rely on conversational memory as the source of truth. Never use an old generated module as product evidence. Generated modules may serve only as series-style references after they pass audit.

Legacy-session guard: a session whose approved output strategy has a product-driven module count but no enabled `continuous_pair_rendering` contract is a pre-v4.1 single-screen run. `generate-next` and `record-generation` must stop it with a restart/recompile instruction; they must never silently continue that session as independent screen generation.

### Compiled-prompt lock

`generate-next` is the only authority that may compose a generation or repair prompt. Pass its `prompt` to the artflow provider verbatim. Do not paraphrase, shorten, expand, rewrite, or replace it with an ad-hoc prompt after user feedback. The only permitted additions are mechanical tool constraints that do not alter meaning, such as the requested raster dimensions or file format.

Use exactly the ordered `reference_images` emitted by `generate-next`. For an initial render without an edit target, the first item is the exact-size blank `format_canvas`; it is a geometry-only generation base, not product evidence or visual inspiration. Keep it first when calling the artflow provider, followed by the emitted product references, so the first raw render starts on the approved aspect ratio instead of relying on prompt wording alone. The generated artwork must cover the whole canvas and must not preserve the blank color or create inner-poster margins. For repair or user-adjustment renders, use the emitted `edit_target` as the edit base and do not invent another canvas. Do not manually substitute a previous generated screen, a mood reference, or a convenient product image. The artflow provider accepts multiple reference images in the declared order, so every emitted reference can be sent; do not improvise a different reference set.

Before calling the artflow provider, verify that the request's `context_contract` names the correct product, current module, approved plan fingerprint, identity fingerprint, series master, and prompt fingerprint. Do not generate when any field is missing or stale.

Also verify, line by line, that the compiled request still contains the approved module duty, lead conclusion, required visual subjects and relations, exact visible-copy whitelist, scheduled reference-expression adaptation, and inherited visual-DNA tokens. Never replace a difficult approved relation with a generic packshot, centered slogan, decorative particles, or a simpler unapproved concept.

### Visual-expression lock

- Preserve the approved screen duty and series passport; user feedback changes only the named delta unless it explicitly invalidates the plan.
- Apply the independent-logo policy across every plan, generation, repair and user adjustment. In a `BRANDED` campaign, M01 must show exactly one clear independent brand logo faithfully matched to the readable user-supplied current-product logo source. In a `BRANDLESS` (white-label) campaign, every screen shows zero independent logos and M01 is identified by the product subject, truthful category/specification lettering and the campaign visual DNA instead. In both modes M02 onward must show zero standalone, floating, corner, header, footer, watermark or repeated logos, and no screen may invent or approximate a brand name or mark. A logo physically printed on verified packaging is part of product identity and remains naturally visible; it does not satisfy the M01 independent-logo requirement and must not be detached or enlarged into a second badge.
- Use imagery as the primary carrier whenever the claim can be shown by product, ingredient, action, scenario, comparison, scale, texture, or spatial relationship.
- When value depends on flow, control, fit, or choice, show the complete relation: source-to-result pathway, trigger-to-feedback-to-result, space-to-clearance-to-usable-state, or buyer-condition-to-option. Do not collapse these duties into generic parameter cards.
- Keep copy concise and bind each phrase to a visible object, action, evidence panel, path, or negative-space zone.
- Vary typography by scene logic: object label, directional annotation, comparison caption, scene caption, evidence caption, edge title, data hierarchy, step sequence, short explainer, or occasional centered hero title. The approved per-screen typography contract is binding; never turn “less text” into one centered headline, one repeated top-left poster, or a decorative picture with no assisted reading.
- Reserve text space during composition. When the approved anchor has no usable space, follow `composition_flexibility`: `LOCKED` fits only inside the declared zone, `NEGATIVE_SPACE_ONLY` uses reserved space without moving proof geometry, and `CONTROLLED_REFLOW` may locally move or recrop supporting subjects to rebuild space while preserving product identity, evidence, duty, and campaign DNA.
- Reject a module that could sell a different product after swapping only the packshot. Product-specific evidence must remain visible and central to the module role.
- Never translate “少文字” into “少内容”. A normal detail module defaults to moderate, layered copy—normally 3–5 information units—plus medium/high visual payload, strong proof relation and standard/high commercial energy. Use a leaner 2–4-unit treatment for hooks, emotional scenes, results and closures only when the picture already communicates the main meaning. Only an explicitly planned breathing screen may be sparse. Additional copy must explain purchase meaning, operation, applicability, compatibility, selection or a truth-changing condition; filler slogans and repeated claims do not count.
- Every normal module needs a visual-proof contract: buyer takeaway, visual verb, primary subject, supporting subjects, required relations, at least three effective visual payloads, product-specific anchors and a hidden-copy test.
- Visual performance remains a hard runtime contract, and v4.1 derives it from story function, visual archetype, module duty, order, product-presence policy, evidence-subject policy, breathing-screen status and visible copy. Omit `module_visual_performance_contract` for ordinary screens. An exceptional override requires `performance_override_reason`, and the compiler limits overrides to a small minority of modules; repeated twenty-field payloads fail compilation.
- Creative permission is affirmative: when supported by current-product evidence, use whole/macro dual scale, truthful state contrast, contact/action moments, real material behavior, source-to-result paths, controlled perspective/occlusion/depth and bounded commercial exaggeration. Permission never creates a new fact, structure, result or test.
- Product/component, action/path, visible result/scale/feedback, material macro and product-bound environment count as visual payload. Decorative ice, snow, glow, arrows, gradients and generic icons do not.
- `visible_copy` is the sole pixel-facing promotional-copy whitelist. Internal module IDs, screen numbers, duties, buyer questions, transitions, planning enums, prompts and repair labels must never enter image pixels, even as negative examples. Do not confuse those workflow labels with small non-claim sequence indices such as `01/02/03` that visibly organize approved action steps.
- Treat `visible_copy.text` as the only renderable copy payload. Roles, anchors, bindings, source notes and layout explanations remain machine-side constraints and must never be phrased as candidate pixel text.
- Enforce the current module's `module_spatial_structure_contract`. A split, stacked, nested, process, orbit, framed-scene, comparison, selection or specification module may not collapse into one full-bleed photograph with floating cards.
- Treat the accepted M01 image as a style-only reference. If its spatial grammar differs from the current module, the harness omits it from image references and preserves continuity through the written series passport and original product evidence.

## Closed workflow

### 1. Start and inspect source material

Automatically select `USER_SELLING_POINT_LED` when the user supplied concrete selling points, otherwise select `PHOTO_TO_PRODUCT_STRATEGY`. In the first route, preserve supplied points and add operating context around them. In the second route, derive a cautious product-operation strategy from product photographs before planning. Never ask the user to choose the route. Follow [product-operation-routing.md](references/product-operation-routing.md), persist the route and operating analysis in the raw input, and split compound statements into atomic facts before coverage planning.

Before creating the session, perform an output-strategy preflight. Cluster every verified fact by buyer question, mark each fact as lead, support, context-only, or deferred, and directly derive one screen count—exactly 8, 10 or 12—from the required buyer-decision jobs and proof depth. Do not create a user-facing single-screen ratio decision: the approved planning ratio is the pair canvas 3:8. Any single-screen dimensions used by the runtime are internal fixed decomposition geometry, not an additional planning choice. A 900×1200 adaptive master and ad-hoc ratio candidates are forbidden.

For an ordinary product-detail request, author `COMPACT_V1_3` with `planning_mode=LIGHTWEIGHT_V41`. Do not build the route-aware operating-analysis ledgers unless the user explicitly asks for buyer diagnosis, positioning analysis, or a strategy report; the product facts, direct screen-count derivation, storyboard modules and visual contracts are sufficient for normal detail-page generation.

Prepare a raw input JSON using `assets/templates/product-input-template.json`. Replace every `REPLACE_*` field. Keep the prefilled internal-review dimensions unless the user explicitly selected another profile or a current backend rule was verified, then run:

```powershell
python scripts/harness.py start --input <raw.json> --session <session.json>
```

Inspect every product image directly. Separate facts into user-confirmed, documented, visually observed, experiential expression, high-risk, conflicting and unsupported routes. Set `coverage_directive` to `MAXIMIZE_USER_SUPPLIED_RELEVANT_FACTS` and `question_budget` to `0`. Do not ask for optional facts. Ask once only if an unresolved SKU conflict or material misleading risk blocks truthful planning.

The input must include `intake_route`, `user_supplied_selling_points`, `operating_analysis`, `conflict_scan`, `source_conflicts`, and `reference_materials`. Analyze composite reference boards at the individual-screen level before recording `observed_expression_methods`; do not treat one collage as one layout. Record reusable expression grammar such as subject scale, object-to-copy anchoring, ingredient or component relationship, action-result pathway, scene logic, comparison structure, evidence placement, type hierarchy, and density rhythm.

### 2. Resolve material conflicts before full planning

If `start` returns `AWAITING_CONFLICT_RESOLUTION`, present one concise batched question containing the conflicting topic, each incompatible value or claim, its source image/document/fact ID, and why the choice changes the truthful plan. Do not include a storyboard, screen count, style proposal, or generation suggestion in this message. Stop and wait.

After the user decides, write a resolution conforming to `schemas/conflict-resolution.schema.json` and run:

```powershell
python scripts/harness.py resolve-conflicts --session <session.json> --resolution <resolution.json>
```

Only retained facts may enter identity locking, selling-point clustering, copy, or visual proof. If there are no material conflicts, proceed without asking.

### 3. Lock product identity

Write the identity contract and run:

```powershell
python scripts/harness.py lock-identity --session <session.json> --identity <identity.json>
```

The identity must state invariant appearance, allowed views/states, uncertain details, forbidden mutations, and `brand_logo_source`. For a branded product, choose a readable current-product image supplied by the user, set `mode` to `BRANDED` (or omit it), its authorization basis to `USER_SUPPLIED_CURRENT_PRODUCT`, and reserve that mark for `FIRST_SCREEN_INDEPENDENT_LOGO`. For a white-label product that the user explicitly confirmed carries no brand mark, set `mode` to `BRANDLESS`, record the confirmation in `brandless_evidence`, and use `image_id: "NONE"`, `authorization_basis: "NOT_APPLICABLE"`, `readability: "NOT_APPLICABLE"`, `usage: "FORBIDDEN_ALL_SCREENS"`; the campaign then runs with zero independent logos and a non-logo first-screen anchor. When neither a readable authentic brand mark nor an explicit white-label confirmation exists, stop before planning and request a usable logo, a clearer product image, or a white-label confirmation; never hallucinate a mark.

### 4. Build and compile the detail-page plan

Use the lightweight compact planning path by default. Reason through only:

`facts -> buyer question -> core claim -> observable proof -> screen order -> visual continuity`

Write a compact authoring plan that conforms to `schemas/compact-detail-plan.schema.json`, then run:

```powershell
python scripts/harness.py compile-plan --session <session.json> --plan <plan.json>
```

Set `planning_mode` to `LIGHTWEIGHT_V41` and `plan_format` to `COMPACT_V1_3`. The authoring plan should focus on the campaign thesis, a short visual passport, a practical screen count, and one compact module brief per screen. The harness fills routine derived dimension, typography, density and performance metadata. Do not spend time writing parallel analysis ledgers that do not change a screen decision.

`plan.json` is a machine input, not the approval document: keep strings concise, omit runtime-derived fields, and do not pretty-print or rewrite it with a deep `ConvertTo-Json -Depth 30` pass. Human-readable expansion belongs only in the compiler-generated `campaign-approval.md`.

The compact plan must preserve `output_strategy`, one campaign visual passport and a full storyboard. In lightweight mode, `operating_strategy`, recall, pain-solution and depth ledgers are optional summaries; keep only the short claim-to-screen mapping needed to avoid losing a verified fact. A fact may be deferred only when it conflicts, is high-risk and undocumented, unsupported, or immaterially duplicated.

For lightweight planning, derive module count in this short order. The result must be exactly 8, 10, or 12:

1. Use only `planning_fact_ids` retained after conflict resolution.
2. Group facts by the buyer question they answer and assign each to the smallest useful screen set.
3. Choose a practical count that covers hook, core proof, use/result and purchase closure; do not create empty screens for ledger completeness.
4. Give every screen one concrete visual proof, one short visible-copy set, and one simple continuity cue. Use deeper sales-depth or formula-split analysis only when it changes the screen count or prevents a material claim error.

Build a short `reference_expression_system` before module details. Record only the references that materially change composition, proof or continuity; do not write a full adoption ledger for references that are merely inspirational.

Every compact module declares its duty, conclusion, claim IDs, story function, pain/value-bridge IDs, one category-neutral `visual_archetype`, concrete proof brief, visual carrier, compact spatial blueprint, exact `visible_copy`, one typography mode and anchor, composition family, a `product_presence_decision`, and a list of visible differences from the previous screen. The presence decision records policy, the unique buyer job if a product appears, the primary subject if it does not, product-specific identity bindings without a full packshot, and the reason for absence. Its `expression` must also decide `product_presence_policy`, `evidence_subject_policy`, `visualization_mode`, `transformation_required`, `transformation_stages` and `truth_boundary`. A pain module additionally declares a compact `pain_expression` selected by the pain's information structure rather than product category: subject type, expression form, pain relation, emotional intensity and why that form fits. Any module with a non-trivial aversion risk also declares `commercial_comfort`, specifying the risk level, sanitization strategy, detail-dominance policy and forbidden visual details. Choose archetypes by buyer job and evidence—not category template—from pain scene, pain-to-solution bridge, benefit overview, dedicated benefit proof, source-action-result, whole-plus-macro, action sequence, use-scope matrix, state contrast, sensory world, selection comparison, trust evidence and purchase closure. The runtime derives copy density, visual-payload density, proof strength, commercial energy, output dimensions, logo policy, copy bindings, full typography fields, carrier sequence, product-presence rhythm, unequal spatial geometry and performance thresholds.

每个模块还必须从 [cross-module-continuity.md](references/cross-module-continuity.md) 的方法库中选择一个 `method_id`，并填写选择依据、接收锚点和交付锚点；不得以“风格统一”或空白字段跳过衔接。规划审批表的“与前后屏关系”列必须展示所选方法及其交付线索。若作者未指定，运行时根据本屏证据载体、结构和故事职责自动选择，并在审批结果和生图提示中明确呈现。

衔接不只检查故事和风格，必须检查真实画面接缝：每个相邻屏都要声明接缝类型、上一屏来源区域、本屏目标区域和接缝几何，并在生图提示中要求同一可见元素在边界处延续、跨越、转折或发生状态变化。只有章节转场可以明确断开；相似配色、相似背景或同一字体不能代替画面接缝。审批表仍在“与前后屏关系”列展示这些信息，但不改变其他规划字段。v4.1 下所有屏幕按两屏一组在同一 3:8 AI 画布中生成；独立 Pair 之间另设共享过渡令牌并在可选整页预览阶段单独验收，且必须填写方法专属接缝证据，不得把“同一风格”误判为像素连续。

Do not make the product packshot compulsory on every screen. Use `REQUIRED_DOMINANT` for the first screen, product/detail/selection screens and purchase closure; use `REQUIRED_SUPPORTING` only when a complete product genuinely participates in the action or bridges a result; use `IDENTITY_TOKEN_ONLY` when a verified partial detail is enough; use `OPTIONAL` or `ABSENT_ALLOWED` for compatibility; and prefer `ABSENT_PREFERRED` for dedicated pain, source, material, mechanism, result, sensory, lifestyle or trust screens when their own evidence needs the visual weight. Product identity is a long-page continuity obligation, not a per-screen occupancy quota. The first and closing screens must return to the complete product, while middle proof screens may omit it entirely. Never satisfy absence with a corner packshot, blurred package, background package, silhouette or floating product card.

For a visualizable efficacy claim, a `DEDICATED_BENEFIT_PROOF`, `SOURCE_ACTION_RESULT` or `STATE_CONTRAST` screen must make the claim object, action or result—not the package—the dominant evidence subject. If the effect is not directly photographable, use `CONCEPTUAL_NON_DIAGNOSTIC`: show an obviously designed, non-laboratory representation of the relevant object and a visible transformation such as contact, surrounding, loosening, separation, blocking, reduction, removal or an end-state contrast. This applies across categories—for example microbes or mites, grease and stains, odor particles, dust, heat, moisture, airflow, pressure, wear, tangles or residue. It may communicate the verified claim family but must not impersonate microscopy, testing, exact morphology, an unverified mechanism, quantified performance or a clinical result. A label beside an icon, a package close-up, or a bottle plus headline does not satisfy dedicated visual proof.

Do not duplicate module data into manual `visual_performance_rhythm` or `spatial_structure_rhythm` ledgers. The runtime derives both sequences and audits the assembled result. First-screen/core-benefit and purchase-closure peaks remain `HIGH`; ordinary screens remain commercially active; only an explicitly planned breathing screen may reduce density. Use enough structure families for readable rhythm, but do not force every screen into a unique template: for eight screens, four or five families is normally sufficient.

Qualification, trust and risk content supports selling; it must not take over the page. Attach conditions to the relevant selling-point screen. A dedicated `TRUST_OR_RISK_REMOVAL` module requires independent `DOCUMENTED_EVIDENCE`; without it, defer the unsupported point or combine a concise qualifier with product/use/selection content. Evidence source, packaging location, missing report, internal risk status and approval workflow must remain machine-side and may not become a module duty, buyer question, lead conclusion or visual-proof direction. A closing screen must return to complete product, core benefit, audience or use result; it may not end as a specification table. `COMPARISON_SPLIT` requires an explicit truthful `comparison_basis`.

Every normal module must set `copy_intent.policy` to `VISUAL_FIRST_WITH_AUXILIARY_COPY`. Each allowed phrase must bind to a named visual object, relation, action, result, comparison or evidence zone. Build a compact information ladder rather than a slogan stack: lead conclusion → short buyer-facing explanation → bound evidence/parameter/use labels → optional condition or selection qualifier. Use copy to orient, explain purchase meaning, disambiguate, quantify or qualify what the viewer sees. Do not repeat a visual fact in a long paragraph, turn every fact into an equal card, or remove useful verified explanation merely to make the page look minimal.

Classify visible entities as hero, companion, accessory, variant, or context-only. For pathway, interaction, installation, selection, comparison, time-series, human-body, or document-evidence modules, record the required relation, evidence source, preserved conditions, and truthful downgrade route before compilation.

Show the user this concise product-operation brief before the decision header:

`采用路由 | 确认的产品事实 | 用户指定卖点 | 推演人群与痛点 | 推荐定位 | 待验证假设`

Then show this decision header before the storyboard:

`目标平台 | 总屏数 | Pair 数量 | Pair 画布比例 | 设计尺寸 | 安全边距 | 固定拆解 | 平台核验状态 | 屏数依据`

Immediately before it, show the three-count comparison:

`屏数决策 | 屏数 | 一级卖点深度 | 证据边界 | 结论`

The storyboard shown for approval must include the actual observable structure:

`模块 | 页面职责 | 主结论 | 画面如何证明 | 商品出镜策略 | 空间结构 | 文字如何进入画面 | 与前后屏关系`

Then show the primary-claim fulfillment matrix:

`一级卖点 | 优先级 | 证据等级 | 总览 | 独立表达 | 使用/结果 | 收尾回忆 | 最终状态`

When the user supplied selling points, also show their coverage ledger. Approval must make a missing or underdeveloped user point obvious before generation.

Run the deterministic preflight before strict compilation:

```bash
python scripts/harness.py preflight-plan --session <session.json> --plan <plan.json>
```

It batches cheap structural, ID, count and mapping errors in one report without mutating the session. Fix the whole report once, then run `compile-plan` exactly once; do not enter a one-error-per-attempt JSON patch loop. `compile-plan` expands the compact input in memory, writes one hierarchical `campaign-approval.md` containing the atomic screen plans followed by their ordered pair plans and internal seam contracts, records `planning_metrics`, and returns the complete document as `approval_markdown`. Show that Markdown verbatim as the complete approval response. Do not paraphrase it, shorten it to a page-order list, rebuild its tables, or add a second handwritten proposal. The compiler writes human-readable Chinese spatial structures and includes all mandatory group and screen fields.

The same preflight must verify that `style_constitution` contains non-empty v4.1 fields `visual_world`, `color_and_light`, `typography_and_graphics`, `product_rendering`, and `rhythm`. Legacy aliases such as `palette`, `typography`, `graphics`, `photography`, and `product_finish` do not satisfy this contract. If any field is missing or blank, or legacy aliases are present, stop before writing the approval packet and ask for a complete recompile; never wait until `generate-next` or silently invent a visual-DNA default after user approval.

After the table, explicitly list every user-supplied selling point that is deferred and its reason. Never hide a deferred “正品”、量化结果、赠品、规格或其他 requested point inside an internal ledger.

Also show one campaign visual-DNA paragraph covering brand anchor, color, typography, graphics, photography and product finish. Summarize which facts were merged and which were deferred, with reasons. Do not expose raw JSON, hashes, prompt text, confidence tables, internal risk maps, or module-level fixed pixel coordinates.

### 5. Stop once for the hierarchical group—screen approval

This is the only planned approval checkpoint before generation. Present the complete hierarchy: total screen count, every atomic screen plan, every adjacent pair, each pair's two screen plans, the 3:8 pair-canvas rule, and the internal seam contract. Pair-to-pair transitions are explicitly deferred to optional assembly/audit. One approval moves the harness directly to `AUTHORIZED`.

Approval command:

```powershell
python scripts/harness.py authorize --session <session.json> --approval-text <verbatim> --approved-module-count <count> --approved-pair-canvas-aspect-ratio 3:8 --approval-document-sha256 <sha256-from-campaign-approval>
```

Do not generate before authorization. If the user questions count, a screen plan, pair grouping, the 3:8 ratio or an internal seam method, remain at this checkpoint and revise/recompile the complete packet.

### 6. Generate sequentially

Request only the next permitted module:

```powershell
python scripts/generation_adapter.py prepare --session <session.json> --request <request.json>
```

For raster generation or repair, explicitly invoke the artflow provider: `python scripts/artflow_generate.py --request <request.json> --out <image.png>` (or `python scripts/artflow_provider.py --request <request.json> --out-dir <dir>` to drive `generation_adapter.py run`). Pass only the compiled current-module prompt and required product references. Never send the full session or all module prompts.

After `generate-next`, read the saved request as UTF-8 JSON and use its `prompt` and ordered `reference_images` without manual rewriting. Re-run the Context rehydration gate immediately before the image-generation call.

In v4.1, request the next permitted adjacent two-screen render container instead of two independent image requests. `generate-next` must compile the two screen contracts first, then produce one `PAIR_PLAN_MERGE` prompt containing two explicit screen windows, a declared internal seam zone, the method-specific seam instruction, and the shared pair-to-pair transition token. The image backend receives only this merged prompt; the per-screen contracts remain embedded for bounded window duties and are retained separately for fingerprints/audits. After the artflow provider returns one 3:8 long canvas, bind the canvas to both module IDs, decompose it at the approved fixed window height for deterministic measurement/export, and audit the pair canvas as one group with both atomic duties and the internal seam in one `pair-judgment.json`. Do not create separate user-facing approvals or repair cycles for the two slices. Never send two ad-hoc screen prompts and assemble them as if they were one AI-generated long image.

Bind the resulting file:

```powershell
python scripts/harness.py record-generation --session <session.json> --token <token> --image <image>
```

For a local provider CLI, use `generation_adapter.py run` so generation and registration are one transaction. For the built-in image-generation tool, call `prepare`, invoke the tool with the unchanged signed request, then call `generation_adapter.py commit` immediately. Recovery of several candidate files from the same active request must use `record-generation-batch`; it cannot import unrequested pairs or skip pixel audit.

`generate-next` is the upstream output-format lock: its request must expose `output_surface` and place an exact-size blank `format_canvas` first for every initial render without an edit target. `record-generation` remains the downstream hard gate. It must read the actual raster dimensions and bind only an exact approved-size module. An exact-ratio or near-ratio image is center-cropped minimally and proportionally resized; a materially wrong-ratio image first receives a bounded symmetric crop, then uses only as much blurred edge extension as the remaining mismatch requires. Never stretch width and height independently, never overwrite the source, and record both original and normalized dimensions plus the normalization mode. This deterministic mechanical normalization happens before pixel audit and does not consume the one visual-repair opportunity. The following pixel audit still decides whether the resulting composition is commercially acceptable.

### 7. Inspect actual pixels

View the pair canvas and write one group judgment conforming to `schemas/pair-judgment.schema.json`. The judgment must first confirm the group canvas is the approved 3:8 size and was decomposed into two fixed single-screen windows; then review the two windows together and record both atomic duties, both screens' selling-point payloads, shared product/fact identity, visible proof, copy binding, pair-level legibility, and one method-specific internal-seam observation. The judgment must preserve upper-to-lower order and distinguish the internal pair seam from deferred pair-to-pair continuity. Internal screen crops may be inspected for legibility and boundaries, but do not create separate user-facing approvals or separate repair cycles for the two screens. When conceptual graphics appear, distinguish harmless stylized visual rhetoric from unsupported scientific or testing evidence. Then run:

```powershell
python scripts/harness.py audit-pair --session <session.json> --judgment <pair-judgment.json>
```

`audit-current` remains available only for legacy single-screen sessions. A pair-level material failure repairs the whole 3:8 canvas once while preserving both atomic duties and the approved internal seam method.

Only material failures may trigger automatic repair: identity/fact error, unsupported claim, fabricated visual proof, unreadable essential Chinese, claim-bearing unauthorized pixel text, leaked workflow metadata, insufficient visual payload, missing product specificity, broken proof relation, underpowered commercial expression, feedback regression, broken text-to-image binding, impossible geometry, source-background leakage, failed module role, severe campaign discontinuity, mechanical adjacent-layout repetition that weakens the approved rhythm, or broken stitching edge. A short non-claim scene caption, consecutive step indices, simple reading aids or harmless variation in a clearly conceptual microorganism/stain/molecule-like symbol is advisory unless it crosses one of those material boundaries.

The audit must also record `planned_elements_visible`, `planned_elements_missing`, `reference_pattern_observation`, and `copy_binding_observation`. Missing approved elements, an unused scheduled reference grammar, or auxiliary copy floating without a visual anchor is a hard failure.

Audit series continuity, semantic-carrier fidelity and spatial distinctness independently. A screen fails continuity when it looks like another campaign; it fails spatial distinctness when an approved composite geometry collapses into one full-bleed photograph or repeats the previous poster grammar. Do not fix one axis by destroying the other.

Do not repair merely for optional information, minor spacing, a different but stronger composition, personal preference, short non-claim pain-scene captions, consecutive `1/2/3` or `01/02/03` step markers, simple arrows/separators, or non-misleading conceptual-shape variation. If removing such an element would preserve the facts but weaken hierarchy, scan order or visual tension, keep the first render. This is proportional tolerance, not permission to add efficacy, safety, quantity, trust or laboratory claims.

### 8. Repair at most once

On `REPAIR_REQUIRED`, create one diagnosis containing every material failure, passed layers to preserve, common root cause, one unified repair strategy, and expected visible change:

```powershell
python scripts/harness.py prepare-repair --session <session.json> --repair-plan <repair.json>
```

Edit the first draft as the repair canvas. Keep original product images as identity references only. This is the sole automatic repair in the current revision cycle. If attempt two still fails, the harness must bind attempt two as `COMPLETE_WITH_RESIDUALS`, preserve every visible residual failure, and immediately authorize the next unfinished module. Do not soften the audit and do not generate attempts three or four. Continuity takes priority over a second interruption: the second draft is the deterministic cycle endpoint, not a claim that the screen passed.

### 9. Apply user corrections as deltas

Convert a user correction into `schemas/user-adjustment.schema.json` and run:

```powershell
python scripts/harness.py apply-user-adjustment --session <session.json> --adjustment <adjustment.json>
```

Reopen only the named module for a local visual/copy change. `change_kind` is required. Compile feedback into `change_only`, `preserve`, and `must_not_regress`; store module-specific feedback under that module only and reserve the global ledger for product identity, approved facts, campaign DNA, and other true campaign invariants. Never append one screen's placement request to every later prompt, and never send the user's raw conversation or diagnosis into the image prompt. An explicit local adjustment opens a new revision cycle with its own two-render ceiling while retaining all prior attempts and failures in history. Replan or restart when product identity, verified facts, target buyer, dominant purchase logic, campaign style, or module sequence changes.

After every user correction, re-run the Context rehydration gate before deciding whether the correction is local. A correction must not silently replace the product, approved story, series passport, evidence policy, typography schedule, or page rhythm. If the user asks to discard previous images or plans, create a clean session from raw inputs and do not pass discarded artifacts to the new run.

### 10. Assemble and audit the long page

After all modules reach either `COMPLETE` or `COMPLETE_WITH_RESIDUALS`, the main delivery can proceed directly to final review. `assemble` is optional and only creates a long-page preview; it is not a required generation or delivery gate:

```powershell
python scripts/harness.py assemble --session <session.json> --output <long-page.png>
```

Assembly must reject any accepted module whose width **or height** differs from the approved per-screen dimensions; width-only checking is insufficient.

If the optional preview is useful, inspect its pixels and record a long-page judgment:

```powershell
python scripts/harness.py audit-long-page --session <session.json> --judgment <long-page-judgment.json>
```

Check story order, repetition, density rhythm, visual-world continuity, lower-page quality, seam integrity, purchase closure, first-screen value clarity, pain-to-solution continuity and whether the must-remember selling points remain recognizable across the assembled page. The compact long-page judgment must separately name and verify pain-module presence, pain-before-solution order, visual-carrier diversity, static-packshot repetition, product-presence rhythm, product identity continuity on absence screens, meaningful product-return points and copy-only justification integrity. In v4.1 it must also cite the real modules and visible pixels that fulfill overview, dedicated proof, use/result and recall for every primary claim. Do not infer these checks from a generic continuity statement. Do not automatically regenerate several modules after long-page audit.
For continuous-pair rendering, the long-page judgment must additionally list each 3:8 pair canvas, its fixed decomposition coordinates, the `PAIR_PLAN_MERGE` request record, method-specific internal seam evidence, and every pair-to-pair transition audit. A pair passes only when both decomposed modules satisfy their own contracts and the seam does not introduce duplicated foreground subjects, duplicated copy, a hard separator, or a visible change of product identity. The method-specific carrier evidence must be concrete and location-based; a generic continuity sentence is not accepted.

Also compare the assembled page against the runtime-derived visual-performance and spatial-structure rhythms: peaks must remain visible, calmer screens must still hold product-specific evidence, lower modules must not show progressive commercial-energy collapse, composite structures must remain recognizable, and full-bleed single-scene repetition must stay within the limit. Only approved breathing screens may intentionally reduce density.

### 11. Final review and delivery

Present the ordered single-screen deliveries, pair canvases, group-level audits, and only material residual issues. Include the optional long-page preview when it was requested. After explicit final approval in ordinary mode:

```powershell
python scripts/harness.py finalize --session <session.json> --output <manifest.json> --approval-text <verbatim>
```

When plan authorization explicitly selected `--delivery-mode AUTO_FINALIZE_AUTHORIZED`, successful completion enters the dedicated `AUTO_FINALIZE_AUTHORIZED` state and uses:

```powershell
python scripts/harness.py auto-finalize --session <session.json> --output <manifest.json>
```

Do not infer this mode from “continue”, silence, or ordinary plan approval.

Use `python scripts/harness.py inspect --session <session.json>` at any time to see the legal next actions.

### Artifact registration paths (Studio OS queue hard rule)

When running inside the Studio OS queue, every artifact reported to the platform must use a
**storage-root-relative path that begins with `projects/<projectId>/`** — e.g.
`projects/<projectId>/artifacts/detail-page/screens/M01.png`, never `artifacts/...` or `work/...`.
The platform's audit gate resolves each registered path against the storage root; a path missing
the project prefix reads as a missing file and the whole delivery is refused as
`artifactIntegrity: no artifact is registered` even though every file exists on disk. Prefer the
platform's unified submission entry (which adds the prefix for you); if you report artifacts with
your own script, prepend the prefix yourself and verify one absolute path resolves to a real file
before finishing.

### Delivery tags (Studio OS queue hard rule)

Tags decide where a file lands in the platform library — `final` means "hand this to the
merchant", so never spend it on derived views. When registering assets:

- `final` goes ONLY to: each accepted single-screen delivery (M01…M10), each accepted pair
  canvas (exactly five for a ten-screen page), and the assembled long-page image itself.
- Long-page slices (the `-slices/` cut of the assembled long page) carry `long-page-slice`, never
  `final` — the long page is the deliverable; its slices are a viewing aid and must not double as
  17 extra finals.
- A pair canvas whose repair revision was accepted (`P03-r2.png`) supersedes its first attempt:
  tag the accepted file `final` and the displaced attempt `superseded`. Never tag both.
- Do not invent tag names; reuse the platform vocabulary (`final`, `superseded`, `screen`,
  `pair-canvas`, `long-page`, `long-page-slice`, `detail-page`).

Incident note (2026-09-18, trash-can project): a run tagged 16 slices `final` and tagged both
`P03.png` and `P03-r2.png` `final`, so the library's FINAL section showed 35 files instead of the
16 real deliverables and the user had to ask for a cleanup.

## Export policy

- Treat width, module height, safe area, format, maximum bytes, and slice height as configuration, not universal truth.
- Prefer lossless PNG while reviewing; create JPEG/WebP delivery variants only when the target backend accepts them.
- Re-check current merchant-backend limits before final platform upload. If authoritative public documentation is unavailable, label the profile `BACKEND_CONFIRMATION_REQUIRED` and ask the user to verify the upload UI rather than guessing.
- Preserve source-resolution masters and create delivery slices separately.

## Completion standard

Complete only when the same product remains stable across modules, verified claims are visually grounded, the page has an intentional purchase story and vertical rhythm, actual pixels have been inspected, group-level pair audits are complete, and the user performs final review. Long-page assembly and audit are optional previews.



## Image provider

This Skill renders through the local artflow provider instead of a host built-in
image tool. Pass the `generate-next` / `prepare` request to it unchanged.

```powershell
python scripts/artflow_generate.py --request <request.json> --out <image.png>
```

To drive `generation_adapter.py run`, use the provider bridge, which prints the
`{"image": "<absolute-path>"}` contract that adapter expects:

```powershell
python scripts/artflow_provider.py --request <request.json> --out-dir <dir>
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

> **状态：已启用硬门禁（B 层）** 本 skill 的 `harness.py` 在 `cmd_lock_identity` 强制 `invariant_features` / `allowed_states` 至少含 3 条具体几何描述（截面/宽高比/结构分段，命中 `GEOMETRY_FACETS` 或数字比例），否则 BLOCKED。出图后由 `visual_gate.py` 里已有的 `require_identity_silhouette` 强制 `identity_silhouette` 块（依据实际可见范围比较：完整商品三项布尔值；身份线索仅比较可见特征；商品缺席三项为 NOT_APPLICABLE，未知不能通过）兜底。两环合起来 = 出图前锁几何 + 出图后锁形态，产品一致性不再依赖模型自觉。

<!-- IDENTITY-CONSISTENCY-LOCK:END -->

审批文件指纹必须覆盖实际 UTF-8 文件字节（含唯一末尾 LF）；授权前同时核对编译器指纹与文件实际 SHA-256，文件被改动必须重新编译和审阅。

### Optional campaign review contract inspection

`inspect-campaign-review-contract --session <session.json>` is read-only. It returns the union of schema-required and dynamically required long-page checks (the latter from the same native function used by `audit-long-page`), the judgment schema, the approved expression-diversity constraints, and the ordered accepted screen/pair byte inventory. It does not assemble a long image, invoke a model, authorize automatic finalization, verify original review provenance, or grant visual acceptance. The website must separately verify the original accepted artifacts and raw review evidence before using this inventory.

Long-page preview and audit remain optional. A consumer must preserve original model observations and actual dispatch/usage evidence and must not fill pass/observation fields from the approved plan. Pair, information, expression, pain/solution, and product-presence contracts can add required checks; a static schema-only checklist is incomplete.

`fork-campaign-review --session <new-session.json> --source-session <owned-source-snapshot.json> --source-session-sha256 <sha256>` creates a separate review snapshot of an accepted campaign. The caller must first verify original C4 review provenance and durable pixels, then preserve the source session's exact bytes in the new owned workspace. The command checks the pinned version, accepted inventory, source byte hash and absence of an unfinished generation. It preserves all accepted modules, identities, facts, original approval and audit references, and writes only the new review session. It clears copied assembly/final controls so optional preview/audit can run in that workspace; it never calls a model, redraws images or grants additional automatic-delivery authority. Repeating the same fork verifies and reuses the existing snapshot without rewriting it.

Native command failures preserve the original `error` text and additionally report `error_type` plus `failure_kind` (`LOCAL_IO` or `CONTRACT_REJECTION`) on stderr. A consumer must keep local storage failure distinct from rejected observations; neither condition grants visual acceptance or another paid request.


## Website direct planning v2

Website requests selecting `WEBSITE_DIRECT_V2` use the small shared contract in
`scripts/runtime/website_plan.py`: one observed intake, one whole-page creative
brief, deterministic native compilation, plan approval, sequential continuous
pairs, fixed slices and the existing six-check website pixel profile. The brief
keeps real fact IDs, short approved copy, visual direction, composition, camera,
product presence and one transition per pair. Do not create pain, formula,
primary-depth or recall ledgers or issue separate pair-completion planning calls.
User facts must be represented; unused photo observations remain identity evidence
and do not become extra independent campaigns. Product truth, identity, dimensions,
approval bindings, original provider outcomes and the one-repair limit remain.
This version replaces the ordinary website planning workload, while historical
contracts and standalone route-aware operation retain their existing behavior.
