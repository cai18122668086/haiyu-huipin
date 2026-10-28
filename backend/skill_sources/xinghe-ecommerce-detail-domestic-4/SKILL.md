---
name: xinghe-ecommerce-detail
description: Use when creating ecommerce detail pages, PDP image packs, Amazon A+ modules, product-photo-to-detail-page frameworks, or direct image generation from product photos. Especially useful for buyer-demand-led ecommerce detail pages, full-category product strategy, standard versus non-standard product differentiation, scene-based high-density layouts, direct selling-point copy, product-consistency and physical-logic locks, deployment-helper-first generation, built-in image_gen as the final fallback only, saving generated files locally, and displaying generated images in the chat.
---

# 星河电商详情4.0

Turn product images, selling points, reference pages, and platform requirements into demand-led ecommerce detail page images. Plan from buyer needs first, choose the purchase decision type, create a concrete page strategy and module plan, match product selling points, build scene-based visual proof, lock one style system, lock product identity and physical logic, then generate images directly by default instead of stopping at prompt output.

Core formula: buyer demand ranking -> purchase decision type -> page strategy -> module planning -> product selling-point match -> page task allocation -> scene evidence expression -> unified visual system -> product identity and physical-logic lock.

## Global Priority Order

When this skill or its reference files contain rules that appear to conflict, interpret and execute them in this priority order:

1. User's explicit request, including requested quantity, platform, size, language, style direction, and module order.
2. Platform compliance and legal safety: do not fabricate claims, certifications, test data, sales, reviews, medical effects, or unsupported parameters.
3. Product truth and product identity: preserve visible product color, structure, material, decoration, proportions, logo/nameplate placement, and physical plausibility.
4. Module Plan count and page strategy: generate one image for each planned module unless the user explicitly specifies another count.
5. Buyer-demand task for the current module: each image must solve its assigned buyer question with one matched selling point and visual proof.
6. Detail-page design strength: avoid plain product posters, repeated templates, empty backgrounds, and unchanged product scale across the set.
7. Reference visual style and creative variation: adapt style, mood, rhythm, and composition devices without copying exact layouts or locking the source photo composition.
8. Optional QA and review notes: disclose issues without automatic regeneration unless the user explicitly asks for regeneration or replacement.

If two same-level rules conflict, choose the option that is more truthful, more compliant, and more aligned with the current module's buyer-demand task.

Direct-generation boundary: every delivered detail-page image must be generated directly by the deployment helper or built-in `image_gen`. Never create delivered images through local stitching, local layout assembly, local text overlay, local background replacement, local product/background compositing, or local crop-and-recombine workflows. If a prompt asks for a collage-style visual device, the complete collage-style module must be generated in one image-generation call, not assembled locally.

## Image Interface Order

- Always attempt the deployment image helper first for final image generation.
- When generating multiple final images through the helper, use the staggered concurrent helper-call rule in `references/generation-tools.md`: submit the first job, wait 3 seconds, submit the next job without waiting for file output, then wait for all jobs after submission.
- Resolve the helper from `XINGHE_IMAGE_GENERATOR` first, then `%LOCALAPPDATA%\ApiCodexOneClick\tools\generate-image.ps1`.
- Use built-in `image_gen` only as the final fallback after the deployment helper is missing, unavailable, fails, or cannot save/display the result.
- Do not call built-in `image_gen` before attempting the deployment helper.

## Core Workflow

## Audit / Regeneration Override

- Disable automatic audit-and-regenerate behavior. After direct image generation succeeds and the files are saved, proceed to delivery; do not regenerate automatically because of quality checks, text issues, layout issues, design strength, or product-consistency concerns.
- Do not require reading `references/quality-control.md` after generation. That file is only an optional manual review checklist and must not trigger automatic regeneration, automatic rejection, or automatic replacement of delivered images.
- If a potential issue is noticed, mention it briefly in the final response under `Optional Review Items / Items Needing Confirmation`. Do not regenerate unless the user explicitly asks to regenerate, redo, re-create, or fix a specific image.
- This override has higher priority than any older rule in this skill or its references that implies mandatory QA, mandatory regeneration, final qualified versions, or regeneration notes.

1. Classify the input:
   - Product images only: start with `Product Image Analysis` and create `Product Consistency Anchors`.
   - Product information is provided: move directly into buyer-demand planning.
   - Reference detail pages are provided: first analyze page roles, information hierarchy, visual devices, and rhythm.
2. Read [references/category-router.md](references/category-router.md) to identify the category, then load only the relevant category structure file.
3. Read [references/buyer-demand-strategy.md](references/buyer-demand-strategy.md) and create `Information Confidence`, `Buyer Demand Map`, `Demand-to-Selling-Point Match`, and `Product Type Strategy`.
4. Read [references/page-planning-strategy.md](references/page-planning-strategy.md) and create `Purchase Decision Type`, `Page Strategy`, `Best Hero Direction`, `Module Plan`, and `Visual Rhythm Plan`. Do not output a material reshoot list.
5. Read [references/page-task-table.md](references/page-task-table.md) and create a screen-by-screen `Page Task Table` from the planned modules before prompt writing. The number of final generated images must follow the `Module Plan` count unless the user explicitly specified a different count.
6. Read [references/scene-layout-standard.md](references/scene-layout-standard.md), [references/style-system-standard.md](references/style-system-standard.md), and [references/product-consistency-physics.md](references/product-consistency-physics.md). Create `Scene Layout Plan`, `Style System Lock`, and `Product Identity & Physics Lock`.
7. Read [references/design-strength-system.md](references/design-strength-system.md) and create a `Design Strength Lock` before image planning: category visual motif, page rhythm, composition contrast, color contrast, and template bans.
8. Read [references/anti-template-check.md](references/anti-template-check.md) and revise the page plan before generation if the set collapses into a repeated template.
9. Read [references/prompt-contract.md](references/prompt-contract.md) to assemble each image prompt. Add the same product-consistency anchors, `Module Plan`, `Demand-to-Selling-Point Match`, `Page Task Table`, `Style System Lock`, `Design Strength Lock`, and `Product Identity & Physics Lock` to every prompt.
10. When the user asks for images, generation, a version, or direct visual output, generate images directly by default without asking whether to proceed.
11. For direct image generation, read [references/generation-tools.md](references/generation-tools.md) and first use the deployment helper. Only if the deployment helper is missing, unavailable, fails, or cannot save/display the result, call built-in `image_gen` as the final fallback. Do not enter an automatic QA or regeneration loop.
12. Handle high-risk claims about efficacy, certifications, sales, reviews, testing, or brand authorization conservatively according to [references/compliance.md](references/compliance.md).

## Hard Rules

- Default to planning `8` detail-page modules, but final generation count must follow the `Module Plan`. If the user specifies a quantity, plan and generate exactly that quantity.
- Every image must use a consistent vertical `3:4` aspect ratio. The first line of every prompt must include `vertical 3:4 e-commerce detail page image`.
- Every screen must contain a main title plus at least one selling-point label or short phrase. Pure visual pages with no meaningful copy are not acceptable.
- Each screen must solve one buyer demand with one core selling point. Material, ingredient, craft, detail, and scene pages may omit the full product, but they still need corresponding selling-point copy and visual proof.
- On-image copy must be direct and short: clear main title, one selling-point phrase, and minimal auxiliary text. Do not use long paragraphs or meaningless decorative English.
- Detail-page planning must start from buyer demand ranking and product type strategy.
- Standard / functional products emphasize utility, structure, operation, efficiency, comparison, and confirmable parameters.
- Non-standard / aesthetic products emphasize design, beauty, material, style fit, pairing, and atmosphere.
- Consumables emphasize taste, texture, use frequency, preparation, packaging convenience, and provided or visible ingredient information.
- Gift or emotional-value products emphasize packaging, ceremony, recipient scenario, display value, and atmosphere.
- Detail pages must not repeat product main images. A default eight-image set must mix at least five page structures; when the `Module Plan` expands to 9-12 modules, keep at least five structures and avoid repeating any one structure more than twice.
- Strong design is mandatory. Every set must define a `Design Strength Lock` and `Style System Lock` first and include at least five clearly different visual roles, at least three composition scales, and at least two memorable visual devices.
- Default layout density is high-density scene-based composition: no meaningless blank space, clear foreground / midground / background hierarchy, readable copy, and scene evidence that supports the highlighted selling point.
- Do not make the complete product the hero on every page. Crops, ingredients, craft, scenes, comparisons, and information cards may each be the main subject of a screen.
- Do not let the full set collapse into the template of same-color gradient background, large top-left title, centered product, and small labels.
- Final images must be generated directly by the deployment image helper or by built-in `image_gen` as the final fallback. Do not use local stitching, local post-processing, text overlay, collage assembly, background replacement, product/background compositing, or crop-and-recombine workflows to create final delivered images.
- Built-in `image_gen` is the last fallback only; helper resolution and helper generation must be attempted first.
- Base product information only on user-provided content and visible facts in the images. Mark reasonable assumptions as `needs confirmation`. `Product Consistency Anchors` lock product identity, not the source photo's composition.
- Product consistency and physical space logic are mandatory: no product drift, no random accessories, no structure deformation, no impossible contact, no contradictory shadows, no wrong scale, and no floating products.
- For apparel, footwear, bags, or accessories with models, prioritize keeping the same person identity, body type, hairstyle, and styling logic. When model display is needed, prefer full-body context.
- Do not use consultation-style CTA copy. Action or closing pages should start from buyer needs and use non-consultation expressions such as daily-use fit, easier pairing, or practical purchase reasons.

## Product Consistency Anchors

When product images are provided, extract and reuse one shared set of product-consistency anchors before generation:

- Overall silhouette: shape, proportions, open/closed state, and major volume relationships.
- Color and material appearance: primary color, secondary color, visible textures such as transparent, metallic, textile, or creamy surfaces, and the relative color-area ratio.
- Pattern, logo, and nameplate: describe only visible position, direction, size relationship, and visual placement. Do not invent unreadable small text.
- Structural parts: positional relationships between handles, knobs, caps, ports, zippers, hang tags, bases, decorative hardware, and other key components.
- Decorative elements: relative positions of lace, patches, patterns, graphics, accessories, and local details.
- Crop-to-whole relationship: detail pages may only enlarge real parts from the original product, not generate a different style from the same category.
- Physical relationship: expected product scale, support surface, contact points, occlusion, shadow direction, and scene-object relationships.

The same detail-page set must reuse the same `Product Consistency Anchors`; do not improvise product identity separately for each image. These anchors preserve product identity only, not the original photo's background, camera angle, crop, product placement, lighting setup, or white-background single-product composition. Backgrounds, compositions, angles, crops, model presentation, scenes, and page structures should change according to the page role, while core product-identifying traits must not change.

If an uploaded product reference is a white-background single-product photo, every generation prompt must explicitly say: `Use the reference image only to lock product identity. Do not replicate the white-background product photo as a white-background single-product image. Create a designed ecommerce detail-page composition with buyer-facing copy and visual proof.`

## Product Image Analysis

When only product images are provided, output first:

- Visible facts: category, color, material appearance, structure, packaging, accessories, SKU, and visible copy.
- Reasonable assumptions: keep them conservative and mark each as `needs confirmation`.
- Invisible information: do not present it as fact.
- Information confidence: classify candidate selling points as `confirmed`, `reasonable inference`, or `needs confirmation`.
- Product type strategy: identify whether the product is mainly standard / functional, non-standard / aesthetic, consumable, gift / emotional-value, or mixed.
- Detail-page directions to develop: six to ten likely buyer questions or concerns.

Treat only information visibly readable on packaging as packaging-visible information. Do not expand it into efficacy claims.

## Reference Page Analysis

When the user provides reference detail pages, analyze five things first:

1. Page type: cover, concept, ingredient, craft, colorway, detail, scene, or summary.
2. Information hierarchy: main title, bullets, explanatory copy, decorative symbols, buttons, or labels.
3. Visual devices: glass frames, hanging display, collage, detail zoom windows, bottom information bands, or technical annotation lines.
4. Rhythm: which pages show the full product and which do not.
5. Reusable logic: learn the structure, but do not copy wording, brand elements, or exact layout details.

## Output Format

For direct image-generation tasks, still provide a brief setup first:

1. `Product Image Analysis` when only product images are provided.
2. `Information Confidence`
3. `Buyer Demand Map`
4. `Demand-to-Selling-Point Match`
5. `Product Type Strategy`
6. `Purchase Decision Type`
7. `Page Strategy`
8. `Best Hero Direction`
9. `Module Plan`
10. `Visual Rhythm Plan`
11. `Campaign Style Lock`
12. `Style System Lock`
13. `Design Strength Lock`
14. `Product Identity & Physics Lock`
15. `Page Task Table`
16. `Scene Layout Plan`
17. `Generated Files`
18. `Delivery Notes / Optional Review Items`

The final response must list local file paths and display generated images in the chat with Markdown image syntax. Do not only say that generation is complete.
