# Aurora high-fidelity adaptation

This package turns a reference image and current-product images into original, route-specific ecommerce artwork. The reference supplies a visual grammar and a decomposed persuasion method; the current product supplies identity, observable features and confirmed claims. Every physical reference panel is analyzed as a buyer question, persuasion job, belief shift, evidence obligation and visible expression action before any product translation is planned.

## Start here

The first operational action is always to create or resume a workflow run. Reading `SKILL.md`, running `--help`, or writing a verbal plan does not activate the workflow, and no image may be generated before `audit-plan` passes and the adapter handoff exists:

```powershell
py -3.12 scripts/entrypoint.py new --base-dir runs --product-input product.png --reference-input benchmark.png
```

Use the emitted `next_action` for subsequent commands. Render only through `scripts/artflow_render.py`, which applies the same gate as `scripts/imagegen_adapter.py`; direct API calls or hand-written prompts outside that bridge are outside the contract.

## Detail-page example

```powershell
python scripts/entrypoint.py new --product-input product.png --reference-input benchmark.png --facts facts.json --base-dir runs
python scripts/entrypoint.py classify-route --workspace <run> --route DETAIL_PAGE
python scripts/entrypoint.py init-reference-analysis --workspace <run> --analysis reference-analysis.json
# Inspect every reference, set physical panel bounding boxes, then crop.
python scripts/entrypoint.py crop-reference-panels --workspace <run> --analysis reference-analysis.json
# View each crop; complete source text/story, sanitized crops and eight observations.
python scripts/entrypoint.py compile-reference-crops --workspace <run> --analysis reference-analysis.json
python scripts/entrypoint.py compile-visual-passport --workspace <run> --analysis reference-analysis.json
python scripts/entrypoint.py register-product-facts --workspace <run>
python scripts/entrypoint.py plan-target-page --workspace <run> --plan detail-plan.json
python scripts/entrypoint.py audit-plan --workspace <run> --audit plan-audit.json
```

For a mixed upload, repeat `--input` for every image. The command registers all files as unassigned; inspect them and confirm roles before classification:

```powershell
python scripts/entrypoint.py new --input a.png --input b.png --input c.png --base-dir runs
python scripts/entrypoint.py inspect-inputs --workspace <run> --role-review role-review.json
python scripts/entrypoint.py classify-route --workspace <run> --route SEARCH_COVER
```

`role-review.json` must identify each registered image by ID and SHA-256, with a visual observation, evidence, and confidence ≥ 0.8. Upload order and filenames are never used to guess product versus benchmark roles.

The default plan is `S01..S10`, grouped as `G01=S01+S02` through `G05=S09+S10`. A deliberate 8–12 screen override must include `--screen-count N --count-evidence "user requested N screens"`. For a carousel, an omitted count follows the number of ordered source content steps (minimum three); an explicit count overrides it. For each group, use `generate-pair-request`, render with `scripts/artflow_render.py` using the emitted request, then `record-pair-artwork`, `audit-pair`, `prepare-revision` at most once if needed, and `accept-pair`. Run `audit-global`, `finalize`, and `export-single-screens` only after all five groups pass.

## Non-detail example

Use `generate-single-request` and the corresponding `record-single-artwork`, `audit-single`, `prepare-single-revision`, `accept-single`, `audit-global`, `finalize`, and `export-single`. A carousel follows the ordered reference content step count when the user gives no count (minimum three); it is never collapsed into one image.

The adapter prepares a native imagegen handoff but does not pretend that deterministic code can judge pixels. See `references/execution-workflow.md`, `references/semantic-translation.md`, and `references/audit-protocol.md`.

Version 3.7.1 adds physical analysis-only panel crops, an exact eight-key template, actionable Passport errors, and recovery with `resume`. Read [reference analysis](references/reference-analysis.md) and, for details, [Pair execution](references/detail-pair-execution.md). A default Pair request draws one tall master containing two screens, not a single screen or a miniature ten-screen preview. Each request now emits `imagegen-prompt-Gxx.txt` and explicitly limits the current target plan to its two screens. Awaiting-artwork requests cannot be overwritten or skipped.

Version 3.7.2 keeps the machine JSON request separate from the model prompt. The Gxx prompt contains only the current two screens, their product facts, relevant content stages, eight visual mechanism observations, and the Pair seam contract; it never embeds the full ten-screen request, hashes, or file paths. Its 12,000-character limit fails closed if target fields are themselves too large.

Version 3.8.0 closes the native-resolution gap found in a real detail run: a 768×2048 image had been upscaled to 1440×3840 and accepted by the old dimension-only check. Detail recording now requires a local receipt generated by `scripts/render_provenance.py`, binding the tool-returned original file to its unchanged artwork copy with verified dimensions and SHA-256; non-fixture audits require that provenance, and fixture audits cannot override a failed real review.

Run `py -3.12 -m pytest -q tests` for deterministic regressions. These tests do not call imagegen or claim visual-quality approval.


Version 3.8.1 makes detail screen and group identifiers machine-only prompt data. G01 can establish a nearby native portrait ratio when the preferred canvas is unavailable; subsequent Pair requests and exported slices reuse that ratio. Non-detail routes retain their existing single-unit prompt flow.

Version 3.8.3 keeps native-file and provenance protections while changing detail Pair geometry to an aspect-only contract: every two-screen master is width:height 3:8, with no fixed 1440×3840 pixel requirement. Prompts carry explicit seam anchors and reference-bound typography instructions so the model continues visible edges, light, background elements and typographic morphology from the benchmark.

### 3.8.2 节奏约束

详情页和其它多屏路由现在逐屏记录密度预算，并为每个相邻屏记录具体边界方式；生成提示词和真实审查分别执行这些约束。
### 3.8.4 recovery continuity

If a process or provider call is interrupted, run `resume --workspace <run>` and then `resume-imagegen-request --workspace <run>`. The latter returns the original one-shot handoff, token and input paths. A missing or changed request, prompt, recovery manifest or source image stops the run for repair; it never creates a replacement request.
### 3.8.5 generation gate

Imagegen handoff requires the run workspace and a passing route plan audit. If planning fails, repair the plan and rerun the workflow; do not use a manually written image prompt.
