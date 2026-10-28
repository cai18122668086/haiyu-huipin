# User-validated commercial baseline v0.1.16

Use this baseline to judge visual performance, not to copy a brand, palette,
camera, headline, offer, product pose, or complete layout.

It distills the transferable behavior observed across ten six-image domestic
e-commerce sets covering beauty, apparel, pet care, seating, storage, food,
office electronics, small appliances, mother-and-baby goods, and charging
accessories.

## Search-cover baseline

Judge these eight axes after inspecting the actual pixels:

1. `product_dominance`: the complete current product is immediately
   recognizable and is the first or joint-first visual subject. It is not
   smaller than a decorative object or background device.
2. `headline_performance`: one short verified click hook is large, confident
   and integrated with the composition. It is not a lightweight caption placed
   beside the product.
3. `physical_evidence`: at least one real current-product material, component,
   content, action, interface or use relation carries the selling point.
4. `spatial_depth`: the image has a credible foreground/midground/background,
   real contact shadow, use relation, or complete-product/detail double scale.
   Empty architecture does not count as depth.
5. `commercial_closure`: a verified specification, service, audience, trust or
   complete offer closes the page. No verified offer means no price theater.
6. `thumbnail_competitiveness`: at a small scan, the product and main claim
   survive as two or three ordered stops without relying on tiny text.
7. `commercial_frame_structure`: a product-derived search composition visibly
   contains identity/header, dominant product-message field and a bottom
   commercial closure. The zones may overlap or synthesize, but none may be
   absent. This is the transferable correction validated by the approved
   Joyoung HU108 cover and the broader search-cover corpus; do not copy its
   brand, orange palette, kettle pose, claims or exact geometry.
8. `type_hierarchy`: brand/identity, click hook or numeric anchor, evidence,
   transaction/specification conclusion and trust closure use at least four
   visibly distinct roles. Plain same-weight text blocks fail.

The mean score must be at least 80 and no axis may be below 65. This gate tests
material commercial recognizability, not aesthetic perfection. Multiple
verified selling points and useful information bars are allowed when hierarchy
is clear. Only a material failed axis requires layout/composition repair; keep
minor differences as advisory observations.

## Carousel baseline

- Identity pages may reduce information density but must retain product scale,
  headline confidence and a truthful human, hand, scene, material or structural
  relation.
- Proof pages must remain understandable when copy is removed.
- Detail pages use real macros, controls, interfaces, material texture,
  contents, connection points or structure. Decorative diagrams are secondary.
- Closing pages maintain the campaign's visual strength while resolving the
  final specification, compatibility, trust or verified offer question.
- A shared Campaign Style means shared typography roles, palette roles,
  material finish and graphic grammar. It never means repeating the same
  layout skeleton.

## Known failure signatures

- compliant six-page roles with weak, generic product proposals;
- broad category prior overriding the current product's form or mechanism;
- user preference present in metadata or a hash but absent from visible tokens;
- small product, weak headline and large empty background;
- abstract glow, air arc, architecture or icons replacing physical evidence;
- search cover reading as an editorial poster;
- search cover preserving the right words but omitting the approved header,
  hero/message field, proof object or bottom closure;
- a selling point stated in copy with no matching physical pixel evidence;
- an accepted weak first page becoming the set's style reference;
- Agent scores describing the prompt instead of visible pixels.
