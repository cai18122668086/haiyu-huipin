---
name: xinghe-ecommerce-detail
description: Create ecommerce detail pages, PDP image packs, Amazon A+ modules, and product-photo-driven campaigns. Use for buyer-objection-driven, non-repetitive detail-page planning and image_gen-first direct generation, or for an external Agent/API batch runtime with mandatory product analysis, validated page planning, gpt-image-2 generation, multi-angle product groups, concurrency, cost estimates, token accounting, resumable jobs, and performance metrics.
---

# 星河电商详情2.0

Turn product images, selling points, reference pages, and platform requirements into ecommerce detail page images with a clear purchase-progression logic. Generate images directly by default instead of stopping at prompt output.

## Image Interface Order

These rules apply to direct image generation inside the current Codex conversation. For an external Agent/API runtime, follow `External Agent/API Runtime` instead.

- Always attempt built-in `image_gen` first for final image generation.
- Do not inspect, test, resolve, or mention the deployment helper before attempting `image_gen`.
- Read or use helper instructions only after `image_gen` is unavailable, fails, or cannot save/display the result.
- If `image_gen` is available, do not run `Test-Path`, `Get-Command`, environment checks, or PowerShell helper commands before generating.

## Core Workflow

## Audit / Regeneration Override

- Disable automatic audit-and-regenerate behavior. After direct image generation succeeds and the files are saved, proceed to delivery; do not regenerate automatically because of quality checks, text issues, layout issues, design strength, or product-consistency concerns.
- Do not require reading `references/quality-control.md` after generation. That file is only an optional manual review checklist and must not trigger automatic regeneration, automatic rejection, or automatic replacement of delivered images.
- If a potential issue is noticed, mention it briefly in the final response under `Optional Review Items / Items Needing Confirmation`. Do not regenerate unless the user explicitly asks to regenerate, redo, re-create, or fix a specific image.
- This override has higher priority than any older rule in this skill or its references that implies mandatory QA, mandatory regeneration, final qualified versions, or regeneration notes.

1. Classify the input:
   - Product images only: start with `Product Image Analysis` and create `Product Consistency Anchors`.
   - Product information is provided: move directly into page planning.
   - Reference detail pages are provided: first analyze page roles, information hierarchy, visual devices, and rhythm.
2. Read [references/category-router.md](references/category-router.md) to identify the category, then load only the relevant category structure file.
3. Read [references/design-strength-system.md](references/design-strength-system.md) and create a `Design Strength Lock` before image planning: category visual motif, page rhythm, composition contrast, color contrast, and template bans.
4. Read [references/prompt-contract.md](references/prompt-contract.md) to assemble each image prompt. Add the same product-consistency anchors and the same `Design Strength Lock` to every prompt.
5. When the user asks for images, generation, a version, or direct visual output, generate images directly by default without asking whether to proceed.
6. For direct image generation, first call `image_gen`. Only if `image_gen` is unavailable, fails, or cannot save/display the result, read [references/generation-tools.md](references/generation-tools.md) for deployment-helper fallback. Do not enter an automatic QA or regeneration loop.
7. Handle high-risk claims about efficacy, certifications, sales, reviews, testing, or brand authorization conservatively according to [references/compliance.md](references/compliance.md).

## Hard Rules

- Default to `8` detail-page images; if the user specifies a quantity, follow the requested quantity.
- Every image must use a consistent vertical `3:4` aspect ratio. The first line of every prompt must include `vertical 3:4 e-commerce detail page image`.
- Every screen must contain a main title plus at least one selling-point label or short phrase. Pure visual pages with no meaningful copy are not acceptable.
- Each screen should focus on one core selling point. Material, ingredient, craft, detail, and scene pages may omit the full product, but they still need corresponding selling-point copy.
- Detail pages must not repeat product main images. A set must mix at least four page structures.
- Strong design is mandatory. Every set must define a `Design Strength Lock` first and include at least five clearly different visual roles, at least three composition scales, and at least two memorable visual devices.
- Do not make the complete product the hero on every page. Crops, ingredients, craft, scenes, comparisons, and information cards may each be the main subject of a screen.
- Do not let the full set collapse into the template of same-color gradient background, large top-left title, centered product, and small labels.
- Final images must be generated directly by `image_gen` or the deployment image helper fallback. Do not use local post-processing, text overlay, collage assembly, or secondary compositing to create final delivered images.
- Never preflight the deployment helper before `image_gen`; helper checks are allowed only after `image_gen` has clearly failed or is unavailable.
- Base product information only on user-provided content and visible facts in the images. Mark reasonable assumptions as `needs confirmation`. `Product Consistency Anchors` have priority over creative variation.
- For apparel, footwear, bags, or accessories with models, prioritize keeping the same person identity, body type, hairstyle, and styling logic. When model display is needed, prefer full-body context.
- Do not use consultation-style CTA copy. Action or closing pages should start from buyer needs and use non-consultation expressions such as daily-use fit, easier pairing, or practical purchase reasons.

## Product Consistency Anchors

When product images are provided, extract and reuse one shared set of product-consistency anchors before generation:

- Overall silhouette: shape, proportions, open/closed state, front/back orientation, and major volume relationships.
- Color and material appearance: primary color, secondary color, visible textures such as transparent, metallic, textile, or creamy surfaces, and the relative color-area ratio.
- Pattern, logo, and nameplate: describe only visible position, direction, size relationship, and visual placement. Do not invent unreadable small text.
- Structural parts: positional relationships between handles, knobs, caps, ports, zippers, hang tags, bases, decorative hardware, and other key components.
- Decorative elements: relative positions of lace, patches, patterns, graphics, accessories, and local details.
- Crop-to-whole relationship: detail pages may only enlarge real parts from the original product, not generate a different style from the same category.

The same detail-page set must reuse the same `Product Consistency Anchors`; do not improvise product identity separately for each image. Product consistency outranks scene creativity. Backgrounds, compositions, and page structures may change, but core product-identifying traits must not change.

When one product group contains multiple angles, reconcile all angles into one anchor set. Do not treat angles as separate products. Mark visible color, structure, or variant conflicts `needs confirmation` instead of inventing a composite product.

## Product Image Analysis

When only product images are provided, output first:

- Visible facts: category, color, material appearance, structure, packaging, accessories, SKU, and visible copy.
- Reasonable assumptions: keep them conservative and mark each as `needs confirmation`.
- Invisible information: do not present it as fact.
- Detail-page directions to develop: six to ten likely buyer questions or concerns.

Treat only information visibly readable on packaging as packaging-visible information. Do not expand it into efficacy claims.

## Reference Page Analysis

When the user provides reference detail pages, analyze five things first:

1. Page type: cover, concept, ingredient, craft, colorway, detail, scene, or summary.
2. Information hierarchy: main title, bullets, explanatory copy, decorative symbols, buttons, or labels.
3. Visual devices: glass frames, hanging display, collage, detail zoom windows, bottom information bands, or technical annotation lines.
4. Rhythm: which pages show the full product and which do not.
5. Reusable logic: learn the structure, but do not copy wording, brand elements, or exact layout details.

## External Agent/API Runtime

Use this section only when the user asks to build, test, deploy, or connect an Agent, relay API, Streamlit app, batch generator, or external `gpt-image-2` workflow.

1. Read [references/agent-api-runtime.md](references/agent-api-runtime.md).
2. Reuse [assets/gpt-image-2-batch-runtime](assets/gpt-image-2-batch-runtime) instead of reconstructing the runtime. Copy the asset into the user's project; do not edit the installed asset in place.
3. Do not call built-in `image_gen` as a preflight for this surface. The user-selected external planner and image endpoints are the intended execution path.
4. Treat one product group as one or more photos of the same product. Send every ordered angle to both the single planning call and each page's image-edit request. Keep different products in separate groups.
5. Require one validated external vision-planning call per product group before any image request. Do not expose or retain a static/template baseline mode in the external runtime.
6. Fail closed when planning fails, returns invalid JSON, omits required fields, or returns the wrong page count. Report the planning error and do not generate that group from fixed fallback screens.
7. Route every detail-page image request to `high`; this skill has no `medium` generation mode.
8. Before planning, show the fixed all-high output-cost estimate. After planning, show the actual pending high count and output cost; the pending medium count must remain zero.
9. Preserve successful files and retry only failed or missing pages. Do not automatically redraw successful pages for subjective quality issues.
10. Store raw and normalized planner/image `usage`. Report planner and image-generation tokens separately, mark omitted usage as unreported, and never substitute a fabricated zero.
11. Keep secrets in environment variables or an untracked local `.env`; never write real API keys into the skill, assets, manifests, or logs.

## Output Format

For direct image-generation tasks, still provide a brief setup first:

1. `Product Image Analysis` when only product images are provided.
2. `Buyer Objection Breakdown`
3. `Campaign Style Lock`
4. `Design Strength Lock`
5. `Screen Frameworks`
6. `Generated Files`
7. `Delivery Notes / Optional Review Items`

The final response must list local file paths and display generated images in the chat with Markdown image syntax. Do not only say that generation is complete.

For Agent/API work, provide the configured project path, launch command, environment-variable names, verified test results, cost range and post-planning cost, planner/image token usage, and the meaning of each performance metric.
