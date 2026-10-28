# Prompt Contract

Each detail-page image must be generated independently. Do not place multiple screens into one image.

## Required Prompt Blocks

Every image prompt must include:

1. `vertical 3:4 e-commerce detail page image`
2. `Campaign Style Lock`: unified palette, typography feel, background system, lighting, icon style, whitespace logic, and product scale.
3. `Style System Lock`: consistent title hierarchy, label style, information-card style, icon/line style, color system, and lighting system across the whole set.
4. `Design Strength Lock`: category visual motif, page rhythm, composition contrast, signature visual devices, color contrast, and template bans.
5. `Product Identity & Physics Lock`: lock product identity traits only: product appearance, packaging, material, proportions, key pattern, logo/nameplate placement, structural-part relationships, scale, contact, shadows, perspective, and occlusion physically plausible. Do not lock the source photo composition.
6. `Buyer Demand`: the single buyer need, concern, or question solved by this screen.
7. `Matched Selling Point`: one direct product selling point that answers the buyer demand.
8. `Information Confidence`: confirmed, reasonable inference, or needs confirmation; never write a needs-confirmation fact as an on-image claim.
9. `Purchase Decision Type`: the selected decision type from the page strategy.
10. `Page Strategy`: the main purchase resistance and conversion angle for the full set.
11. `Module Plan`: module number, module title direction, buyer question, core information, visual suggestion, copy suggestion, required proof, and risk reminder for this screen.
12. Current page role: cover, pain point, scene, ingredient, craft, detail, specification, comparison, FAQ, closing, or similar.
13. Current page main title: a short direct title in the buyer's language, shown inside the image.
14. Current page selling-point label or short phrase: one clear item that supports the page's core selling point.
15. Current page visual proof: scene, comparison, action, material macro, structure detail, process flow, information card, ingredient / texture setup, style pairing, or other proof method.
16. Current page composition: full product, crop, macro, human / object scale, ingredient, craft, scene, information card, collage, or similar.
17. Current page difference: state how this page differs from the previous page in background, subject scale, visual device, title placement, and information density.
18. Negative constraints: do not fabricate sales, certifications, testing, reviews, medical efficacy, parameters, material composition, package quantity, compatibility, or brand authorization; do not use consultation-style CTA copy.

## Product Identity & Physics Lock

Every prompt must reuse the same lock, including at least:

- `silhouette lock`: preserve overall shape, width/height/depth proportions, rounded corners or edges, and open/closed state.
- `pattern/logo placement lock`: preserve relative position and direction of patterns, logos, nameplates, labels, and visible copy; keep unreadable text as visual placement only and do not invent words.
- `shape/proportion lock`: preserve size relationships between the main body, accessories, base, handles, ports, and decorative parts.
- `relative position lock`: preserve top/bottom, left/right, front/back, surrounding, centered, edge-aligned, and other positional relationships between key parts.
- `color/material lock`: preserve primary color, secondary color, color-area ratio, and visible material appearance such as transparent, metallic, textile, paper, food, powder, liquid, or creamy surfaces.
- `physical logic lock`: preserve believable support, contact, shadow, occlusion, perspective, gravity, hand grip, wearing fit, pet interaction, and scale relationships.
- `do-not-change list`: explicitly forbid changing the product into another style from the same category, adding unprovided structures, deleting key patterns/nameplates, moving decorative elements, changing the core silhouette, or adding random accessories.
- `composition freedom`: allow and require changes to camera angle, crop, scene, background, props, model display, information layout, typography hierarchy, and page rhythm when they support the current page role.
- `white-background non-replication`: if the uploaded reference is a white-background product photo, explicitly forbid recreating it as a white-background single-product image; convert it into a designed ecommerce detail-page image with headline, selling labels, and visual proof.

Detail, material, craft, and scene pages may omit the full product. When a product crop appears, it must come from the same product and must not become a replacement product from the same category.

## Page Copy Rule

- Each screen must communicate one buyer demand and one matched selling point.
- On-image copy should be short, direct, and in the buyer-facing language implied by the user's request; do not place long body text.
- Titles should prioritize buyer language over product-category names.
- Selling-point labels should be one short phrase or one short sentence.
- Material, ingredient, craft, detail, and scene pages may omit the full product, but they must still include a title and selling-point label.
- Decorative text, garbled text, blank labels, fake badges, or meaningless English do not count as valid selling-point copy.

## Design Strength Rule

- Establish and reuse a `Design Strength Lock` and `Style System Lock` for the full prompt set. Do not rely only on generic words such as premium, clean, or modern.
- Every image must specify a clear visual device, such as speed lines, material magnifier, cutaway window, floating card, scene depth, exploded structure, ingredient setup, annotation line, comparison zone, or hand operation.
- Every image must specify subject scale. In a default eight-image set, no more than four images should use a large full-product hero; for 9-12 planned modules, keep full-product hero pages below half of the set.
- Every image must state how it differs from the previous page: change at least two of background system, product angle, title position, image/text ratio, subject scale, information density, or device type.
- Strong design should come from composition, light/shadow, scene depth, material evidence, scale, process, and visual proof, not fabricated selling points.

## Layout Diversity Rule

A default eight-image set must mix at least five structures. For 9-12 planned modules, preserve at least five structures and avoid repeating any one structure more than twice:

- Hero poster.
- Scene immersion.
- Enlarged local detail.
- Ingredient, material, or style flat lay.
- Craft/process flow.
- Parameter, checklist, or specification information card.
- Comparison or objection-handling page.
- FAQ or closing summary.
- Usage process or hand-operation page.
- Category motif page, such as track, kitchen, bathroom, desk, living room, vanity, outdoor setting, gift scene, or storage scene.

Do not make every page follow the same centered-product, top-left-title, bottom-label structure.

## Planned Count Rule

Generate one image for each row in the `Page Task Table`. The final image count must equal the `Module Plan` count unless the user explicitly requested a different count.

Do not output a separate material reshoot list. Missing evidence belongs in module-level risk reminders or final optional review notes.
