# Pain-to-solution visual story contract

This contract is category-neutral. Use it for appliances, food, beauty, apparel, home, pet, digital products, services and every other sales detail page. Category examples illustrate the logic; they never authorize a product claim.

## The purchase-decision chain

A normal sales detail page must make the buyer travel through a visible decision chain:

`need or pain -> product solution -> truthful proof -> mechanism or reason -> use/result -> risk removal -> selection or purchase closure`

Not every link needs a separate screen. Screen count still follows fact clusters and proof needs. The pain link, however, must be scheduled as either:

- `DEDICATED_PAIN_MODULE`: a distinct screen first makes the problem, friction or undesired state visible, then a later screen answers it with the product;
- `COMBINED_PAIN_SOLUTION_MODULE`: one screen visibly contains both the problem state and the product-caused transition or result;
- `EXPLICITLY_NOT_APPLICABLE`: allowed only when the user explicitly requested a non-sales information/specification/trust page. “No user data”, “hard to visualize” and “need fewer screens” are not exemptions.

Pain hypotheses may define a safe scene or buyer context. They do not create product efficacy. The product side of the bridge must cite one or more verified `value_bridge_ids`.

After choosing the link type, read [pain-expression-routing.md](pain-expression-routing.md). Select the expression from the problem's subject, visibility, multiplicity, relation, consequence and emotional intensity—not from product category. `PAIN_SCENE` is a story duty, not permission to default to one full-bleed lifestyle photograph.

## What counts as a visible pain

A pain is visible only when the picture shows a specific affected object/person/environment, an undesired state or action, and its consequence. A heading, icon row, emoji, word cloud, floating label or generic worried face is not a pain scene.

Examples of valid visual grammar across categories include: a stained fibre before cleaning; a cable tangle blocking desk use; an overflowing drawer; a pet refusing an unsuitable texture; glare preventing screen reading; a garment pulling at a movement point. These are scene grammars, not permission to claim that the current product solves them.

## Module visual-expression modes

Every module declares exactly one mode:

- `IMAGE_FIRST`: the picture carries the claim; copy only orients or names what is already visible.
- `IMAGE_PLUS_COPY`: the picture carries the physical state/relation while short copy qualifies, quantifies or disambiguates it.
- `COPY_ONLY_JUSTIFIED`: permitted only for `TEXT_VERIFIABLE_ONLY` material such as exact legal wording or dense specifications, with a concrete justification. It must still use an appropriate document/specification visual carrier, never a decorative poster.

If a claim is `VISUALIZABLE` or `PARTIALLY_VISUALIZABLE`, `COPY_ONLY_JUSTIFIED` is forbidden.

## Visual carrier vocabulary

Choose one primary visual carrier per screen:

`PRODUCT_HERO`, `PAIN_SCENE`, `ACTION_CONTACT`, `STATE_CONTRAST`, `PROCESS_PATH`, `MATERIAL_MACRO`, `RESULT_SCENE`, `SCALE_RELATION`, `DOCUMENT_EVIDENCE`, `PRODUCT_DETAIL`, `USE_SCENE`, `SELECTION`, `SPECIFICATION`, `TRUST`, `PURCHASE_CLOSURE`.

The carrier is the semantic engine, not a background decoration. A normal page of six or seven screens must use at least three carrier classes; eight or more screens must use at least four. No more than two consecutive modules may be static product packshots.

Product prominence is duty-aware. The product is normally dominant on the hero, product-detail, selection and closure screens. On a pain, mechanism, action or result screen, the truthful carrier may be the first visual subject and the product the second, but the screen must still close the visual path back to the product benefit.

## Required module contract

Each module records:

- `story_function`, `pain_ids`, and `value_bridge_ids`;
- `expression_mode` and `claim_visualizability`;
- one `primary_visual_carrier`;
- one concrete `visual_scene_sentence` describing what the viewer sees without relying on copy;
- one `visual_semantic_payload` describing what the viewer understands with copy hidden;
- observable `required_visual_relations`;
- the limited `copy_responsibility`;
- at least five `forbidden_substitutes`, including text-only explanation, icon-only explanation, decorative atmosphere, generic packshot substitution and equal card tiling.

Pain modules additionally record the compact `pain_expression` defined in `pain-expression-routing.md`. Preserve backward compatibility when resuming an older approved plan, but all newly authored compact plans must make this routing decision explicit.

## Pixel hard gates

Reject the screen when any applicable condition is true:

- the observed carrier differs from the planned carrier;
- the core meaning disappears when copy is hidden;
- a visualizable claim depends mainly on copy or icons;
- a pain screen lacks a visible problem state, consequence or product-solution bridge;
- an action, contrast, process or result carrier shows no visible state change;
- a proof screen could sell another same-category product after swapping only the packshot;
- the screen is only a static packshot plus promotional copy;
- decorative elements are counted as semantic evidence.

The long-page audit independently verifies pain-module presence, pain-before-solution order, carrier diversity, static-packshot repetition and copy-only justification. A general statement such as “pain-to-solution continuity exists” is not evidence; the judgment must name the visible modules and relations.


