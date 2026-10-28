# Continuity and variation audit

## Dual-axis judgment

Judge every generated module on two independent axes.

### Series continuity

Inspect:

- palette roles;
- typography character and hierarchy logic;
- brand-anchor continuity without logo repetition: in a `BRANDED` campaign M01 may contain one authorized independent logo, in a `BRANDLESS` campaign it contains none and relies on the product subject and visual DNA; every later screen must contain zero independent logos in both modes, while package-printed logos remain natural product identity;
- photography, light and finish;
- graphic, annotation and information-surface language;
- product rendering and perceived price tier;
- the module's declared inherited visual-DNA tokens.

Fail when the screen looks like another campaign even if it is attractive by itself.

Treat any independent, floating, corner, header, footer or watermark logo on M02 onward as a material failure, and any such logo on any screen of a `BRANDLESS` campaign as the same material failure. Do not fail a later module merely because the verified package itself still displays its printed logo.

### Page distinctness

Inspect:

- composition family;
- camera scale;
- text mode and placement;
- reading path;
- proof device;
- density and visual emphasis;
- declared variation from the previous module.

Fail when a screen mechanically repeats the previous poster structure and the repetition weakens the story.

### Spatial structure fidelity

Inspect the actual zone count, geometry, structural relationship, full-bleed status, dominant/secondary panels, mini-scenes and similarity to the preceding module. Fail when a planned split, stack, nested board, process-to-result, orbit, framed-scene, comparison, selection or specification structure appears as one continuous photograph with overlaid copy or generic cards. Judge geometry independently from subject, carrier and color.

### Rolling three-module window

For every current module, compare it with the preceding two modules on four axes:

1. `duty`: the buyer question and commercial job;
2. `carrier`: product, action, scene, pathway, comparison, document, data, material, or selection;
3. `camera`: hero/wide, medium relation, close detail, macro, orthographic information, or diagram;
4. `text mode`: hero headline, object label, directional annotation, comparison caption, scene caption, evidence caption, step sequence, or data hierarchy.

Treat repetition on three or four axes as a strong warning. Pass it only when a written transition reason shows that repetition improves comprehension, such as a controlled same-condition comparison or a necessary step sequence. Otherwise vary the carrier first, then camera or text mode, while preserving the approved duty and visual DNA.

Do not enforce this as a blind numeric runtime gate. Judge story purpose and visible result.

## Text integration

Inspect whether:

- text placement follows subject, action, evidence or negative space;
- copy is bound to what it explains;
- a display headline is justified by page duty;
- essential Chinese is accurate and readable;
- decorative copy does not overwhelm visual proof.

## Proof integrity

Inspect whether visible product contents, texture, ingredients, actions, results, reports and comparisons are supported by the declared evidence class. Treat photorealistic fabricated proof as a material failure.

## Repair routing

- Low continuity, adequate distinctness: restore visual-DNA tokens; preserve composition and proof.
- Adequate continuity, low distinctness: change composition, camera, text mode or reading path; preserve visual DNA.
- Poor text integration: reposition or restructure copy around the existing visual relation; do not automatically restyle the scene.
- Fabricated proof: remove or replace the unsupported visible state; preserve all truthful passed layers.
- Three-window repetition: retain the strongest module, then change the weakest neighbor's carrier, camera, text mode, or density without inventing a new claim.

Do not solve one failed axis by destroying an axis that already passed.

## Pair seam evidence is method-specific

For continuous-pair campaigns, do not accept a generic continuity sentence as evidence. Read the approved `transition_contract` for the upper screen of each pair and verify the declared carrier at the fixed boundary. The long-page judgment must report, for every pair, the method ID, concrete source and target regions, carrier, geometry, hard-cut absence, and the method-specific boolean required by `cross-module-continuity.md`. Then report every independent pair-to-pair boundary with its approved transition token, visible shared token, source/target regions, carrier, and hard-separator result.

If the generated pixels show a scene cut where the plan called for `EDGE_BLEED`, `PRODUCT_OVERFLOW`, `PERSPECTIVE_TURN` or `SUBJECT_STATE_HANDOFF`, mark the seam as failed even when the two halves are individually attractive. A `PASS` long-page judgment is invalid unless every internal and pair-to-pair audit contains concrete location-based evidence and all required booleans are true.


