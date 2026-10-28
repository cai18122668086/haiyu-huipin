# Design Strength System

Read this file before direct detail-page image generation. The goal is to avoid generic templates while keeping the system compatible across categories, so final images have a clear buyer-demand strategy, visual proof, page rhythm, and category memory point.

## Audit / Regeneration Override

- This file only handles pre-generation design planning and prompt constraints. It does not define post-generation audit behavior.
- Disable the rule that weak design requires regeneration. If weak design, repeated layout, copy issues, or physical-logic issues are noticed after generation, record them only as optional review items and do not regenerate automatically.
- Rewrite prompts and generate again from this file only when the user explicitly asks to regenerate, redo, re-create, or fix a specific image.

## Core Principle

Strong design is not decoration stacking. It means every screen has a clear conversion role:

- The opening page needs an immediate purchase reason, not just a centered product.
- A selling-point page should make one buyer demand and one product answer obvious.
- A detail page needs material evidence, structure evidence, craft evidence, or usage evidence.
- A scene page needs realistic use context and physical logic, not a fake background swap.
- An information page should feel like a designed confirmation card, not a spreadsheet screenshot.
- A closing page should summarize purchase reasons, not become a generic CTA.

## Demand-Led Design

Before designing visual roles, use the `Buyer Demand Map`, `Demand-to-Selling-Point Match`, and `Page Task Table`.

- Standard / functional products: prioritize practical function, structure, operation, efficiency, comparison, and confirmable parameters.
- Non-standard / aesthetic products: prioritize design, beauty, material texture, style pairing, body / space fit, and atmosphere.
- Consumables: prioritize taste, texture, preparation, package convenience, usage frequency, and visible / provided ingredient information.
- Gift / emotional-value products: prioritize packaging, ceremony, recipient scenario, display value, gifting logic, and atmosphere.

## Design Strength Lock

Before generating a set, write one `Design Strength Lock` and reuse it in every prompt. It must include:

1. `category visual motif`: the most recognizable visual motif for the category and product style.
2. `buyer-demand rhythm map`: the demand progression across the set, such as impact / core concern / scene proof / material evidence / objection / process / info / summary.
3. `composition contrast`: at least three composition scales across the set, such as wide scene, medium product, human/object scale, extreme close-up, overhead, cutaway, collage, information card, or deep scene perspective.
4. `signature device`: at least two recurring devices, such as energy lines, cutaway windows, floating labels, material magnifiers, hand-drawn annotations, environmental props, beams, motion trails, layered cards, comparison zones, or process arrows.
5. `color contrast`: main color, accent color, dark anchor, highlight color, and intentional readable text zones.
6. `scene density`: high-density scene composition with purposeful foreground / midground / background layers and no meaningless blank space.
7. `template ban`: explicitly ban repeating centered product, top-left title, small labels, pale gradient, and unchanged product scale across the full set.

## Category Motifs

Choose motifs by product category. Do not reuse one visual language for all categories:

- Sports/outdoor/footwear/apparel: speed lines, track arcs, energy trails, dynamic angled cuts, real outdoor ground, fabric movement, body fit, or localized sport-friction texture.
- Beauty/personal care: texture macro, glossy or matte finish, ingredient still-life setup, soft light bands, bathroom/vanity scenes, hand-use process, or clean but not empty whitespace.
- Food/beverages: flying ingredients, cutaway or brewing process, real table scene, steam, liquid flow, serving scene, or origin/ingredient setup.
- Pet products: real home environment, pet interaction, feeding/play process, size relationship, cleaning/storage scene; avoid exaggerated anthropomorphism.
- Home/daily-use products: spatial order, before/after comparison, storage modules, hand operation, material close-up, or daily-life flow.
- Electronics/tools: dark technical background, exploded structure, port close-ups, sweeping light, specification card, connection process, or use-scenario desktop.
- Baby/toys: soft sense of care, parent-child daily moments, material close-ups, size relationship, play scene, or storage scene; avoid absolute safety claims.
- Gifts/emotional products: packaging, receiving moment, table display, seasonal setting, ceremony, and warm atmosphere.
- General categories: decide the motif from the product's core form plus the buyer's main concern; do not default to mint green or a generic gradient.

## Page Rhythm Requirements

Recommended rhythm for the default eight-module plan:

1. `Impact Cover`: strong hero visual with the strongest purchase reason and category motif.
2. `Core Demand`: answer the highest buyer concern with a direct selling point.
3. `Scene Proof`: realistic use scene, style scene, or pain-point scene.
4. `Material / Structure Evidence`: material, ingredient, surface, craft, structure, port, texture, or local relationship.
5. `Objection Handling`: buyer concern page expressed through comparison, annotation, FAQ, or information card.
6. `Use Process`: usage process, pairing process, wearing process, preparation, connection, storage, or continuous scene action.
7. `Spec / Info Card`: include only provided or visible information.
8. `Closing Summary`: summarize purchase reasons and visually close the set.

If the `Module Plan` selects a different quantity, or the user specifies a different quantity, compress or expand with the same logic while preserving demand differences, layout differences, and scene evidence.

## Prompt Design Rules

Every prompt must specify:

- the buyer demand solved by this page;
- the matched selling point and confidence level;
- the visual proof method;
- the visual role of this page within the full set;
- subject scale: full product, half product, local macro, distant scene, human/object scale, overhead, or information card;
- visual device, such as large diagonal speed slash background, macro circular lens window, comparison zone, exploded material layer cards, hand operation, or style scene;
- copy hierarchy: position and size of title, selling-point label, and any minimal explanatory copy;
- scene density: foreground / midground / background layers;
- physical logic: contact, shadow, perspective, occlusion, scale, and realistic interaction;
- difference from the previous page: avoid repeating the same background, angle, and title-zone placement.

## Template Ban

Avoid the following before generation. After generation, do not automatically rewrite prompts or regenerate:

- Most images use the same pale gradient background.
- Most pages use a top-left title, centered product, and small labels at the bottom.
- The complete product appears at nearly the same scale on every page, with insufficient detail/scene/information/process variation.
- Pages look like main images instead of detail explanations after the first screen.
- Decorative lines and cards do not support the selling point and only fill space.
- Category visual motif is weak, such as sports shoes without speed or scene, food without appetite or ingredients, beauty products without texture and light, or electronics without structure and technical order.
- The scene is unrelated to the buyer demand or selling point.
- The visual is high-density but unreadable.

## Direct Final Image Rule

Final deliverable images must be generated directly by the image tool or API. The only allowed local file handling is:

- Downloading, copying, renaming, and saving to local paths.
- Checking dimensions and file existence.
- Regenerating affected images within the specified scope after the user explicitly asks.

Do not use local scripts to stitch images, overlay text, make collages, replace backgrounds, composite product/background layers, create contact sheets, or crop/recombine images into final deliverables or default preview outputs. If a collage-style page is needed, generate the complete collage-style page directly through the image tool/API. If final images have text errors, insufficient design strength, physical-logic problems, or product drift, record them only as optional review items by default; solve them through stronger prompts or regeneration only when the user explicitly requests correction.
