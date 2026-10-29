# Changelog

## 4.0.1

- Fixed `plan-target-page` crashing when reference analyses represent `source_semantics` as plain strings; both string and `{id,...}` entries now normalize safely.
- Added fail-closed, actionable validation for flattened `semantic_translation` objects and reports the exact missing source-semantic IDs.
- Clarified the required per-source-semantic translation shape in the Skill and semantic-translation reference.

## 4.0.0

- Converged the workflow state model by removing the unused `TARGET_PAGE_PLAN_READY` state; planning now proceeds directly to `PLAN_AUDIT_REQUIRED`.
- Made `next_action` surface persisted generation blocks instead of suggesting generation after a failed plan audit.
- Defined typography precedence: safety/form bans override reference-font inheritance while allowed proportions, weight, spacing, hierarchy and carriers remain reference-derived.
- Kept current Pair source-step coverage and sanitized-crop identity exclusion as one fail-closed generation contract.
- Added regression coverage for the blocked next action and retained the default 10-screen, five-Pair, 3:8 detail contract.

## 3.9.7

- Blocked image generation whenever a persisted `generation-block.json` says the plan/audit failed, even if workflow state was incorrectly changed to `GENERATION_ALLOWED`.
- Required non-fixture sanitized crops to declare `source_product_presence=false` and explicit source-step bindings; generic mechanism crops no longer drive a detail Pair.
- Required each current detail Pair request to cover every mapped source step in both `target_reference_sequence` and sanitized crop bindings, preventing benchmark product/scene leakage from mis-scoped attachments.
- Added regression coverage for blocked-state bypass and missing product-pixel exclusion.

## 3.9.6

- Added a typography-form contract banning handwritten, brush, calligraphic, marker and cursive lettering like the supplied example.
- Kept the ban limited to the visual font form; benchmark-matched copy, specifications, evidence, usage and density remain required.
- Added regression coverage proving an audit with `forbidden_typography_form: true` cannot pass.

## 3.9.5

- Promoted the unconfirmed product name, logo, watermark and wordmark ban into a machine checked generation and audit contract.
- Added regression coverage proving a visual audit with `unconfirmed_product_mark: true` cannot pass.

## 3.9.4

- Added an explicit full-width upper/lower Pair geometry contract so detail masters cannot be interpreted as left/right columns or long multi-section strips.
- Made each screen's recorded density budget a hard upper bound against extra copy blocks, detail images, parameter rows and secondary focal objects.
- Added a native-generation ban on unconfirmed product names, logos, watermarks and wordmarks on the product itself, with matching audit guidance and regression coverage.

## 3.9.3

- Scoped each generation request to the current target's ordered benchmark crops instead of attaching the entire reference set to every Pair.
- Added a target reference sequence contract and prompt section so single-screen benchmark pages are assembled in physical order with explicit source steps, functions, expression actions and handoffs.
- Added a non-fixture guard that rejects a long sequence flattened into one repeated persuasion/evidence/expression template while allowing genuinely repeated benchmark treatments.
- Rejects Unicode replacement-character corruption in non-fixture source method fields instead of letting unreadable analysis drive generation.
- Added regression coverage for target crop scoping, ordered source prompts and flattened-sequence rejection.

## 3.9.2

- Clarified the decorative-label ban in native prompts and detail guidance: benchmark hierarchy and rhythm are preserved, but pill, capsule, badge, sticker, label-cloud and repeated rounded-card carriers are translated into open editorial text, annotations, rules or columns instead of reproduced.
- Removed the remaining positive prompt example that asked generation to preserve capsule titles or numbered badges.
- Added regression coverage for the prompt-level label-carrier conflict.

## 3.9.1

- Added a four-tier copy-compliance contract: ordinary product descriptions remain allowed without external test reports; evidence-gated claims require bound facts; category-sensitive terms receive category review; clearly prohibited language fails closed.
- Added plan and generation-request checks so normal material, color, texture, size, capacity and usage copy is not overblocked while unsupported patent, certification, quantified, ranking, comparison and strong safety/efficacy copy cannot pass silently.
- Removed the overstrict priority-fact and exactly-once coverage gate: patents, certificates, tests and other uploads are optional fact sources, and the benchmark decides whether they appear.
- Removed the forced source-expansion note and duplicate-copy rejection: repeated benchmark treatments remain valid, while differentiated benchmark steps still must remain differentiated.
- Kept benchmark-derived information density, required warnings and existing detail-page narrative contracts intact.

## 3.9.0

- Added evidence inventory for multi-image product inputs so patents, certificates, tests and product-information pages must become confirmed fact IDs before planning.
- Added priority evidence coverage: detail plans must place each priority patent/certificate/test/proof fact on a target screen exactly once.
- Added source-expansion progression checks: further units need distinct benchmark-derived expression or evidence notes, while duplicated complete copy fails even after an intervening unit. Semantic audits also reject paraphrased repetition.
- Kept the final screen’s closing/takeaway duty internal; words such as “收束”, “总结” and “结尾” are prohibited from rendered copy.
- Added regressions for evidence omission, repeated priority facts and repeated source-step copy.

## 3.8.9

- Corrected the 3.8.8 label ban so it removes only pill, capsule, chip, sticker, badge, label-cloud and repeated boxed label carriers.
- Restored ordinary explanatory copy, specifications, evidence, usage text and benchmark-matched information volume; the ban no longer implies shorter copy or fewer visual elements.
- Removed the Skill-defined universal premium visual contract. Premium feel, density, hierarchy and screen expansion now derive from the observed benchmark mechanisms and source density records.
- Added regression coverage that generation requests carry reference-derived density/style and do not carry the former universal premium contract.

## 3.8.8

- Added a mandatory activation gate: applicable requests must create or resume a persisted run before planning or image-tool use.
- Clarified that reading the Skill, running `--help`, searching code, or writing a verbal plan does not count as workflow execution.
- Restricted native image generation to the adapter handoff after a passing `audit-plan` and active generation request; direct native calls and hand-written prompts are outside the contract.
- Added regression coverage for the activation gate and updated the interface default prompt to surface the required first action.

## 3.8.7

- Added method-level opening-function classification so `result_promise` hero openings are distinguished from `problem_awareness` pain openings.
- Enforced preservation of the observed opening function in non-fixture detail plans and carried it into the generation prompt.
- Kept the rule conditional on the actual reference: references that begin with pain may still begin with pain.
- Added non-fixture semantic regressions for both result-promise preservation and valid problem-awareness openings; the tests use generic synthetic duties and contain no test-thread product facts.
- Removed the detail-route requirement for caller-authored per-screen buyer questions, answers, duties, visual roles and evidence bindings. Detail prompts now derive each screen's expansion from its bound reference steps, density and transitions; other routes keep their own target contracts.
- Added a global decorative-label ban and premium visual contract for every route. Generation requests and prompts reject icon-plus-short-word filler, pill/badge rows, sticker tags, label clouds and unsupported certification badges, while preserving necessary product/specification text as integrated editorial copy.

## 3.8.6

- Made reference decomposition method-first and route-agnostic: each physical panel records conversion stage, buyer question, persuasion job, belief shift, evidence obligations and visible expression actions.
- Added semantic handoff and next-buyer-question fields so visual seams cannot substitute for narrative continuity.
- Required non-fixture target plans to bind each screen's buyer question, answer, belief shift, required visual roles and evidence bindings; generation prompts now carry these obligations into the current target.
- Kept fixture compatibility limited to deterministic structural tests; no product-specific facts or claims were added to the Skill.

## 3.8.1

- Enforced native high-resolution detail masters with local original-file comparison receipts, exact dimensions, unchanged SHA-256, and no resize/upscale acceptance. Receipts are not provider-signed.
- Required render provenance for non-fixture detail audits and prevented fixture evidence from overriding a prior failed real review.
- Added CLI, schema, and regression coverage for missing, invalid, and valid native render receipts.
- Kept S/G identifiers in machine state only; prompts explicitly forbid rendering them. If G01 cannot use the preferred canvas, later Pair jobs and export slices inherit its accepted native portrait ratio. Other route prompts remain single-unit and are covered by a regression check.

## 3.7.2

- Fixed oversized detail Pair handoffs: `imagegen-prompt-Gxx.txt` no longer embeds the full generation request, hashes, paths, or ten-screen plan. It is a bounded current-target prompt with a 12,000-character fail-closed limit.
- Added an explicit Pair continuity contract for shared visual world, carryover, narrative handoff and seam behavior. The prompt now states the upper/lower regions and continuity rules instead of treating the two screens as unrelated images.
- Kept the complete request JSON for machine validation, while separating it from the concise native imagegen prompt. Added regression coverage for prompt size/content and continuity schema.

## 3.7.1

- Added exact reference-analysis scaffolding and physical full-panel cropping commands for every route, separated from sanitized generation references. Reused/mismatched panel files and incomplete panel-to-content coverage fail closed.
- Passport diagnostics identify missing/unknown/empty mechanism fields and invalid crop citations. Normal failures preserve state and can be corrected and retried; resume reports the next action.
- Detail generation explicitly binds one vertical Pair master to its two current screens, screen regions, and readable native imagegen prompt. Pending requests cannot be overwritten or skipped, and file hashes are checked again before token issuance.
- Preserved detail defaults (10 screens, 5 Pairs) and reference-derived carousel count; retained explicit counts from new through classification. Fixed banner alias audit lookup.
- Synchronized analysis/generation schemas, added deterministic overview/Pair recovery regressions, and excluded pytest caches from distribution. No real imagegen or visual-quality pass is claimed for this patch.

## 3.7.0

- Carousel output count now follows the ordered reference content sequence by default, with a minimum of three units.
- Explicit user count overrides reference-derived count while requiring complete source content coverage in the target plan.

## 3.6.0

- Added mandatory set-level source content analysis with per-panel verbatim text, content roles, normalized messages, progression and handoffs.
- Bound every target unit to source content steps so reference storytelling is learned across all routes.

## 3.5.0

- Strengthened visual fidelity with an explicit eight-mechanism style signature in every generation request.
- Real visual audits now require per-mechanism scores; generic style similarity is insufficient.

## 3.4.0

- Added native high-resolution render contracts for every route.
- Required one-pass model-rendered text and imagery; post-text overlays and low-resolution enlargement are rejected.

## 3.3.0

- Added overview/contact-sheet detection for every route.
- Composite benchmark images now require ordered physical panel crops before visual or narrative analysis.

## 3.2.0

- Added complete multi-reference carousel decomposition and explicit source ordering.
- Bound carousel narrative units to ordered benchmark references and included the sequence in generation requests.

## 3.1.0

- Fixed mixed multi-image input identification. Repeated `--input` files are now unassigned until a per-image, SHA-256-bound role review confirms product and benchmark roles.
- Added `inspect-inputs` CLI command, input-role-review schema, source benchmark binding checks, and success/failure coverage.

## 3.0.0-vnext

- Rebuilt all route execution around a typed, fail-closed workflow.
- Changed the detail default from historical four screens/two groups to ten screens/five Pair groups.
- Added explicit product-driven semantic translation and source-world leakage rejection.
- Added ordered carousel and multi-format set contracts, artwork hash binding, provenance-aware audits and clean packaging.


## 3.8.2

- Added physical-panel density budgets and adjacent transition evidence. Target plans carry exact visible-copy budgets and per-boundary treatments into prompts; real visual audits reject card-grid inflation or generic repeated seams. Legacy fixture defaults are labeled structural-only and do not claim visual quality.
## 3.8.3

- Detail Pair geometry now validates the native width:height `3:8` ratio without requiring 1440×3840 pixels; clarity guidance keeps native output and rejects resizing.
- Pair prompts now require visible seam anchors and explicit reference-bound typography morphology, hierarchy and carrier treatment.
- Imagegen handoff reports the detail aspect contract and clarity guidance instead of a fixed requested pixel size.
## 3.8.4 — interruption-safe generation recovery

- Commit an awaiting artwork token before external handoff and write JSON atomically.
- Persist `artifacts/recovery-manifest.json` with request/prompt hashes and run identity.
- Add `resume-imagegen-request`; `resume` validates the exact request and bound inputs and fails closed on loss or drift instead of restarting generation.
## 3.8.5 — generation gate after plan failure

- Add a workspace-bound imagegen gate requiring `GENERATION_ALLOWED`, a passing `audit-plan`, and the active target request.
- Record `generation-block.json` when plan or plan-audit commands fail, making the stop condition explicit.
- Reject hand-written or stale requests in `imagegen_adapter.py`; add regression coverage.

