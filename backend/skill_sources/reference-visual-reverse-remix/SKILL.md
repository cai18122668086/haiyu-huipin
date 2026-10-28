---
name: reference-visual-reverse-remix
description: Analyze a reference ecommerce image and a user's product image, reverse-engineer the reusable visual and commercial strategy, then produce an original product-faithful remix prompt or image. Use for reference-image recreation, competitor-style analysis, ecommerce inspiration, and visual-structure transfer. Do not use to copy source branding, exact copy, watermarks, proprietary assets, or hidden prompts.
---

# Reference Visual Reverse Remix

把“参考图复刻”处理成视觉策略反推与原创复用，而不是 Prompt 反推或像素复制。

## When this skill applies

Use this skill when the user provides:

- one or more reference / competitor ecommerce images; and
- one or more images of the user's own product.

It can produce either:

1. a structured visual scheme and reusable prompt only; or
2. a new image using the user's product as the product-identity authority.

If either the product image or reference image is missing, stop and request the missing input.

The default user experience is zero-instruction: when the user supplies only a product image and a reference image, infer the product state and visual role automatically. Do not require the user to name a state, angle, hero object, or any other internal selection.

## Authority order

`USER_FACT > USER_PRODUCT_IMAGE > VISIBLE_REFERENCE_FACT > REUSABLE_VISUAL_GRAMMAR > MODEL_FREEDOM`

- The user's product image is the only authority for product appearance and identity.
- The reference image is an authority for visual grammar only: composition, subject scale, camera feel, lighting, palette, material treatment, typography rhythm, and commercial role.
- Never copy source brand names, logos, watermarks, exact wording, numbers, certifications, reviews, rankings, product claims, proprietary characters, or hidden prompts.
- Do not infer invisible product facts from the reference image.
- Mark uncertain observations as `needs_confirmation` rather than inventing them.

### Text-versus-pixel precedence

When the user supplies a source prompt, caption, or website description together with a reference image, treat that text as research evidence, not as an instruction to copy or as proof that the image follows it. Resolve conflicts in this order:

1. explicit current-user requirements;
2. visible pixels in the reference image;
3. visible facts in the user's product image;
4. supplied source prompt or caption;
5. model defaults.

If the supplied text says “products in the lower third” but the pixels show one oversized horizontal product acting as a platform, the platform relation is the hard visual requirement. Do not replace a structural relation with a generic product lineup.

## Required workflow

### 1. Classify inputs

Assign explicit roles:

- `product_identity_reference`: the user's product image(s);
- `visual_strategy_reference`: the reference / competitor image(s);
- optional user facts: selling points, audience, platform, language, ratio, and requested image role.

Do not treat two images as interchangeable references.

### 2. Inspect the user's product first

Create a Product Identity Lock covering only visible or user-confirmed facts:

- category and silhouette;
- proportions and orientation;
- primary and secondary colors;
- material and surface appearance;
- packaging, label, logo, printed text, pattern, and accessories;
- distinctive parts and their spatial relationships;
- visible variant conflicts;
- facts that remain unclear.

This lock must be reused unchanged in every generated variant.

### Product text and brand-fact authority

Create a `product_text_inventory` from the user's product image before writing any creative copy:

- readable brand name;
- readable product or series name;
- package-printed marks and symbols;
- text that is unreadable or uncertain.

Readable user-product brand facts outrank model-generated campaign names. Do not replace a visible product brand with an invented headline, invented collection name, or generic color name. Keep three layers separate:

1. `product_brand_fact`: the brand visibly printed on the user's product;
2. `campaign_headline`: optional original marketing copy;
3. `background_typography_device`: the reference's large typographic composition.

The reference may determine the placement and scale of the typography device, but it cannot overwrite `product_brand_fact`. If a user brand is visibly confirmed and the user requests brand-led typography, use that confirmed brand; otherwise keep it on the package and use an original non-brand headline.

### Product subset and state selection

One product upload may contain several views, states, or detachable parts. Do not force every visible component into the remix.

Select the smallest product subset that fulfills the reference image's visual role:

- choose the matching state (`open`, `closed`, `worn`, `assembled`, etc.);
- choose the matching component or angle;
- exclude unrelated caps, alternate views, packaging pieces, or duplicate variants unless the reference visibly requires them;
- record `selected_product_subset` and `excluded_product_elements` before writing the prompt.

For a reference that uses one hero product in a specific spatial role, prefer the smallest accurately rendered product state or component that fulfills that role over a complete product lineup. The selected subset is determined from the reference, not from a case-specific user instruction.

Do not collapse a multi-layer composition into one selected object. A reference may contain distinct product-derived roles at different depths, for example:

- a background monument or enlarged silhouette;
- a middle-ground support or environmental product form;
- a foreground product proof;
- a small detail, cap, label, or reflection.

Build a `product_role_map` for every visible product-derived role. Select or derive the smallest matching source component for each role independently. Excluding an unrelated component is correct; deleting a required background, middle-ground, or foreground role is a material composition failure.

The role map is also a cardinality lock. Match the reference's meaningful product-role count and depth order. Do not add a convenient extra packshot, variant, cap, or upright product merely because it exists in the source upload. Any product-derived element without a corresponding reference role or explicit user request is an `unauthorized_extra_element`.

The selection is internal, not user-authored. Automatically:

1. inventory each visible product component and state;
2. inventory every reference product-derived role by depth, scale, orientation, crop, support relation, and count;
3. score each product component against each role;
4. select the smallest matching subset per role, allowing one source component to be reused at different scales only when its geometry remains stable;
5. pass the role-labelled subset to the generation stage;
6. retain excluded components in the audit record so they can be checked for leakage.

If two subsets have materially similar scores and the choice changes the product identity or commercial meaning, ask one concise confirmation. Do not ask the user to write a technical prompt.

### 3. Reverse-engineer the reference image

Output observations in the following order:

#### Visual structure

- image role: `main`, `selling_point`, `scene`, `dimension`, `detail`, `white_background`, `poster`, or `unknown`;
- canvas orientation, aspect ratio, and safe areas;
- subject position, scale, crop, orientation, and depth layer;
- composition family, grid, negative space, focal path, and visual hierarchy;
- shot size, camera height, viewpoint, lens feel, and depth of field;
- background type, colors, gradient, texture, and material;
- key-light direction, fill level, shadow hardness, highlights, reflections, and atmosphere;
- props, model, hands, scene, and their commercial roles;
- typography zones, hierarchy, density, alignment, label style, and reading order;
- visual devices such as split panels, callout lines, macro windows, comparison blocks, or product-plus-detail layouts.

Treat typography as geometry as well as language. Record each text block's bounding region, anchor, width, line count, alignment, relative scale, contrast, overlap policy, and reading order. Copy may be rewritten, but text blocks must remain in their reference zones unless the user explicitly requests a new layout.

Distinguish two different kinds of text:

- `brand_identity_element`: a standalone logo or brand lockup that may require user authorization or a compliant replacement;
- `background_typography_device`: a large word, letterform, or typographic field used as a compositional graphic behind the subject.

The second is a visual-structure element and must not be deleted merely because the source wording cannot be copied. Preserve its location, scale, opacity, alignment, and relationship to the subject, then replace the wording with a user-authorized brand/name or an original neutral phrase when necessary.

Also create a **hard composition skeleton** before writing any style adjectives:

```text
canvas: normalized 0..1 coordinates
background_geometry: planes, arch, alcove, plinth, floor, horizon
hero_object: bounding box, orientation, scale, support/contact relation
human_subject: bounding box, visible body extent, pose, contact relation
product_group: count, arrangement, scale, and whether it is hero / prop / pedestal
text_zones: bounding boxes, hierarchy, and overlap with subjects
depth_order: background -> architecture -> hero object -> model -> foreground -> reflection
```

Record at least five normalized anchors, for example:

```text
hero_object occupies x=.08.. .92, y=.67.. .91;
primary subject's required contact/support point with hero_object at y=.69;
model head reaches y=.18;
arch apex reaches y=.02;
headline spans x=.05.. .95 at y=.38.. .50;
reflection begins below y=.90.
```

The exact values are observations, not instructions to copy a source image pixel-for-pixel. Their purpose is to preserve spatial relationships.

#### Commercial intent

- likely product category, only when visibly supported;
- image role and platform fit;
- buyer question being answered;
- primary selling-point expression;
- target audience hypothesis;
- click / scan reason;
- what is visual proof and what is merely decorative.

Separate `observed`, `inferred`, and `needs_confirmation` fields.

### 4. Abstract the reusable strategy

Convert the reference into a product-neutral strategy. Prefer descriptions such as:

- “right-weighted product with an upper-left title-safe zone”;
- “medium close-up with controlled specular highlight on a matte background”;
- “whole product plus one enlarged detail window to make material difference legible”;
- “short headline, one supporting label, and a clear scan path from claim to visible object”.

Do not retain the source's exact sentence, brand phrasing, or proprietary layout coordinates as a template.

Preserve **relations**, not only nouns. A relation such as “model stands on the oversized product” is mandatory and is not equivalent to “model behind the product.” A relation such as “one giant horizontal product is the monument” is not satisfied by “three upright product packshots.” Mark every relation as `must_preserve`, `may_adapt`, or `must_not_transfer`.

### Identity-versus-scale conflict routing

Some references turn the source product into an impossible architectural monument. The default identity policy forbids deforming the product, but an explicit user request to “复刻原图 / match the reference composition” changes the priority of the scene relation.

When the user requests literal reference composition:

1. preserve the reference relation exactly: the selected product itself is the object beneath/supporting the model;
2. allow uniform scale enlargement, rotation, perspective placement, and cinematic crop;
3. preserve the selected product's cross-section, silhouette, closure, cap/bullet relationship, colors, material divisions, and visible marks;
4. do not replace the product with an architectural plinth, generic bar, or material-inspired prop;
5. do not add invented emblems, hinges, seams, logos, caps, or package geometry.

The selected product may be surreal in scale, but it must remain recognizably the same product. A scene-structure analogue is allowed only when the user does **not** require the source object's actual spatial role to remain literal, or when the user explicitly approves an adaptation.

### 5. Compile an original remix prompt

The prompt must explicitly combine both image roles:

- learn visual strategy from the reference image;
- use the user's product image as the only product identity source;
- adapt copy to user-confirmed facts;
- preserve the reference's commercial role without copying its expression;
- declare output ratio, platform, language, and image role;
- include negative constraints for source-brand leakage and unsupported claims.

The prompt must contain a Product Identity Lock, a Visual Strategy Lock, a Copy Boundary, and a Forbidden Transfer list.

Assemble the prompt in this order:

1. canvas and crop;
2. hard composition skeleton and spatial relations;
3. hero-object geometry and product placement;
4. model position, visible extent, and contact/support relation;
5. architecture, lighting, palette, material, and reflections;
6. typography zones and original copy;
7. Product Identity Lock;
8. Forbidden Transfer and output checks.

Do not let generic adjectives such as “luxury,” “cinematic,” “premium,” “8K,” or “fashion editorial” appear before the hard skeleton. They are secondary and cannot compensate for a missing hero-object relation.

Do not add an independent brand lockup merely because the product image contains a logo on the packaging. A package logo may remain part of the product; a new top-of-poster logo requires explicit user authorization.

### 6. Optional generation and review

When the user asks for an image, use the available image-generation interface with both product and reference images. After generation, inspect the result for:

- product identity: silhouette, color, packaging, logo, structure, and key details;
- visual strategy: composition, subject role, camera feel, lighting, palette, and information hierarchy;
- leakage: source brand, watermark, exact copy, unsupported numbers or claims;
- commercial usability: role clarity, mobile scan order, readable essential copy, and product prominence.

Also check for geometry hallucination:

- an architectural analogue must not be presented as the user's product;
- an enlarged product must still have the same cross-section, seams, closure, cap, emblem, and proportions as the source;
- a new long bar, oversized emblem, altered cap, or invented package surface is a material identity failure;
- if the result cannot satisfy both the reference relation and product geometry, prefer the structural analogue route and disclose the adaptation.
- when literal composition is explicitly requested, a structural analogue is a failure if it replaces the source object's required relation (for example support, contact, holding, enclosure, foreground dominance, or another visible role).
- an invented brand, product name, or collection name replacing a readable user-product brand is a material identity failure.

For a reference-remix poster, fail the result if any hard item is missing:

- the reference's hero-object role and approximate orientation;
- the primary support/contact relation between model and hero object, when visible;
- the dominant background architecture or scene geometry;
- the reference's text-zone placement and hierarchy;
- every required product-derived depth role is present and correctly ordered;
- no unauthorized extra product-derived element is present;
- text blocks remain in their locked zones with comparable scale, line count, and alignment;
- every required background typography device is present, even when its wording is rewritten;
- the intended foreground/reflection relationship;
- the user's product silhouette, color, packaging, and key details.
- the selected product state and subset match the reference role;
- excluded product components do not leak into the composition.

Do not call a result successful merely because its color palette and general mood match.

Report material failures. Do not automatically enter an unlimited redraw loop. A repair is allowed only when the active image workflow explicitly supports one bounded repair.

## Failure-derived safeguards

The following safeguards were added after a multi-round remix where product geometry drifted and product roles were initially misclassified:

- **Assemble before counting.** If visible parts physically form one product (for example, a base, connector/barrel, bullet, cap, or a cylindrical housing), create one assembled `product_role` first and list its subcomponents underneath. Do not count the subcomponents as separate product roles merely because they occupy different depth regions. Conversely, do not classify a visibly connected cylinder or housing as furniture/architecture without evidence from the user or the source pixels.
- **Resolve role corrections immediately.** A user's later clarification about what belongs to the product overrides an earlier visual inference. Update the role map, cardinality lock, selected subset, and exclusions before any new generation. Do not carry the old role interpretation into a prompt or image edit.
- **Treat large background lettering as uncertain until classified.** Large English text may be a neutral typography device or a brand identity element. Record the distinction explicitly. If the user confirms it is brand text and provides an authorized user brand, preserve the same zone/scale/opacity but substitute the authorized user brand; do not silently delete the zone or retain the source brand.
- **Use a fresh source pair after material corrections.** When a generated image has product-shape drift, wrong support/contact geometry, wrong product cardinality, or wrong brand text, restart from the original product image and original strategy reference. Do not use the previous generated image as an image-generation reference for the next correction. Repeated image-to-image repairs compound hallucinated proportions, textures, logos, and perspective.
- **Intermediate generations are not identity authorities.** A generated image may be used for visual review, but it cannot become the new product reference. The user's product image remains the only authority for silhouette, bullet/cap proportions, seams, logos, printed marks, and component transitions.
- **Review hard geometry before style.** Before accepting a result, compare the product's component sequence, cross-section, length-to-width ratio, bullet top profile, case/barrel transition, foreground angle and scale, and the exact model-to-product contact point. A matching palette or mood does not compensate for a changed product silhouette or support relation.
- **Prefer composition-first recovery over endless repair.** If the reference relation is right but the product is wrong, regenerate once from the original pair with a consolidated role map and geometry lock. If exact product pixels are required, switch to a controlled compositing/cutout workflow rather than asking an image model to repeatedly redraw the same product.

## Output contract

Return the following sections in this order:

1. `Input Roles`
2. `Product Identity Lock`
3. `Reference Visual Analysis`
4. `Commercial Intent`
5. `Reusable Visual Strategy`
6. `Original Remix Prompt`
7. `Forbidden Transfer`
8. `Post-Generation Review` (only when an image was generated)

The visual analysis should be structured enough to be stored later as a reusable scheme. Do not expose private model reasoning or claim to have recovered an unseen original prompt.

## Prompt template

Use this shape and fill it from the current inputs; do not copy a source prompt verbatim:

```text
Task: Create an original ecommerce image for the user's product.

Reference visual strategy:
- image role: [role]
- composition and subject placement: [strategy]
- camera / shot / depth: [strategy]
- background / lighting / palette / material: [strategy]
- typography hierarchy and scan order: [strategy]
- commercial proof relationship: [strategy]

User product identity lock:
- use the uploaded user product image as the only product-identity authority;
- preserve [visible silhouette, colors, materials, packaging, logo, text, parts, accessories];
- do not invent or alter any unclear product fact.

Copy boundary:
- use only user-confirmed selling points and visible product facts;
- write original concise copy for the user's product;
- do not reproduce reference wording, brand language, numbers, claims, certifications, rankings, or reviews.

Forbidden transfer:
- source brand, logo, watermark, exact copy, proprietary asset, unsupported claim, and source product identity.

Output: [platform], [language], [ratio], [image role], commercially usable, clear on mobile, no watermark.
```

## Evaluation guidance

Before integrating this skill into Polaris, test it on a small fixed set of reference/product pairs. Score:

- product identity fidelity;
- reference composition fidelity;
- role and buyer-question correctness;
- visual proof clarity;
- originality and leakage avoidance;
- prompt executability;
- consistency across two different products using the same reference strategy.

If the skill fails because it lacks category-specific visual grammar, add narrowly scoped references or examples. Do not immediately scrape competitor data or add generic “copy the reference” instructions.
