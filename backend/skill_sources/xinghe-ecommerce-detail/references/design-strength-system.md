# Design Strength System

Read this file before direct detail-page image generation. The goal is to avoid generic templates while keeping the system compatible across categories, so final images have a clear visual strategy, page rhythm, and category memory point.

## Audit / Regeneration Override

- This file only handles pre-generation design planning and prompt constraints. It does not define post-generation audit behavior.
- Disable the rule that weak design requires regeneration. If weak design, repeated layout, or copy issues are noticed after generation, record them only as optional review items and do not regenerate automatically.
- Rewrite prompts and generate again from this file only when the user explicitly asks to regenerate, redo, re-create, or fix a specific image.

## Core Principle

Strong design is not decoration stacking. It means every screen has a clear visual role:

- The opening page needs a memory point, not just a centered product.
- A selling-point page should make one message the visual hero, not simply attach labels to the product.
- A detail page needs scale impact, material evidence, or structural evidence.
- A scene page needs realistic use context, not a fake studio background swap.
- An information page should feel like a brand manual or specification card, not a spreadsheet screenshot.
- A closing page should summarize purchase reasons, not become a generic CTA.

## Design Strength Lock

Before generating a set, write one `Design Strength Lock` and reuse it in every prompt. It must include:

1. `category visual motif`: the most recognizable visual motif for the category.
2. `rhythm map`: the intensity rhythm across eight images, such as impact / scene / macro / information / comparison / detail / spec / closure.
3. `composition contrast`: at least three composition scales across the set, such as wide shot, medium shot, extreme close-up, overhead, cutaway, collage, information card, or deep scene perspective.
4. `signature device`: at least two recurring devices, such as energy lines, cutaway windows, floating labels, material magnifiers, hand-drawn annotations, environmental props, beams, motion trails, or layered cards.
5. `color contrast`: do not rely on a single pale gradient. Include a main color, accent color, dark anchor, and intentional whitespace ratio.
6. `template ban`: explicitly ban repeating centered product, top-left title, small labels, and pale gradient across the full set.

## Category Motifs

Choose motifs by product category. Do not reuse one visual language for all categories:

- Sports/outdoor/footwear/apparel: speed lines, track arcs, energy trails, dynamic angled cuts, real outdoor ground, or localized sport-friction texture.
- Beauty/personal care: texture macro, glossy or matte finish, ingredient still-life setup, soft light bands, bathroom/vanity scenes, or clean whitespace.
- Food/beverages: flying ingredients, cutaway or brewing process, real table scene, steam, liquid flow, or origin/ingredient setup.
- Pet products: real home environment, pet interaction, feeding/play process, size relationship, cleaning/storage scene; avoid exaggerated anthropomorphism.
- Home/daily-use products: spatial order, before/after comparison, storage modules, hand operation, material close-up, or daily-life flow.
- Electronics/tools: dark technical background, exploded structure, port close-ups, sweeping light, specification card, or use-scenario desktop.
- Baby/toys: soft sense of care, parent-child daily moments, material close-ups, size relationship, or play scene; avoid absolute safety claims.
- General categories: decide the motif from the product's core form plus the buyer's main concern; do not default to mint green or a generic gradient.

## Page Rhythm Requirements

Recommended rhythm for the default eight detail-page images:

1. `Impact Cover`: strong hero visual with category motif and product recognition.
2. `Buyer Scene`: real use scene or pain-point scene.
3. `Material Evidence`: material, ingredient, or surface macro.
4. `Structure Detail`: structure, craft, port, texture, or local relationship.
5. `Objection Handling`: buyer concern page expressed through comparison, annotation, or information card.
6. `Use Process`: usage process, pairing process, or continuous scene action.
7. `Spec / Info Card`: include only provided or visible information.
8. `Closing Summary`: summarize purchase reasons and visually close the set.

If the user specifies a different quantity, compress or expand with the same logic while preserving rhythm differences.

## Prompt Design Rules

Every prompt must specify:

- The visual role of this page within the full set; do not only write detail page.
- Subject scale: full product, half product, local macro, distant scene, overhead, or information card.
- Visual device, such as large diagonal speed slash background, macro circular lens window, or exploded material layer cards.
- Copy hierarchy: position and size of title, selling-point labels, and a small amount of explanatory copy.
- Difference from the previous page: avoid repeating the same background, angle, and title-zone placement.

## Template Ban

Avoid the following before generation. After generation, do not automatically rewrite prompts or regenerate:

- Most images use the same pale gradient background.
- Most pages use a top-left title, centered product, and small labels at the bottom.
- The complete product appears at nearly the same scale on every page, with insufficient detail/scene/information/process variation.
- Decorative lines and cards do not support the selling point and only fill space.
- Category visual motif is weak, such as sports shoes without speed, food without appetite appeal, or beauty products without texture and light.

## Direct Final Image Rule

Final deliverable images must be generated directly by the image tool or API. The only allowed post-processing is:

- Downloading, copying, renaming, and saving to local paths.
- Checking dimensions and file existence.
- Regenerating affected images within the specified scope after the user explicitly asks.

Do not use local scripts to overlay text, make collages, replace backgrounds, or crop/recombine images into final deliverables for the purpose of improving design or fixing copy. If final images have text errors or insufficient design strength, record them only as optional review items by default; solve them through stronger prompts or regeneration only when the user explicitly requests correction.
