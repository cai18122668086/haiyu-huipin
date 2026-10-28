# Spatial structure contract

## Purpose

Control the observable geometry of each module separately from its story duty, semantic carrier and visual style. Different subjects inside the same full-bleed poster are not structural variation.

Use this relationship:

`verified meaning + spatial structure + visual performance + series DNA = a distinct but coherent sales screen`

## Module contract

Every module declares a compact `module_spatial_structure_contract` with:

- `structure_family`: the observable arrangement, not an abstract mood;
- `zone_briefs`: one or more short descriptions of the meaning-bearing regions;
- `structure_difference_from_previous`: a visible geometric change, not merely a new subject or background;
- `forbidden_fallbacks`: concrete regressions that would collapse the approved geometry.

The runtime derives zone count, structural relationship, full-bleed permission and dominant/secondary/mini-scene thresholds from the selected family. Full v0.9.2 contracts remain accepted for exceptional cases, but they are no longer the normal planning burden. The series-master layout-inheritance boundary is a fixed runtime rule and cannot be weakened by plan JSON.

Useful structure families include:

- `FULL_BLEED_HERO`: one dominant commercial stage; reserve for a real peak;
- `SPLIT_PANEL`: two unequal side-by-side regions;
- `STACKED_BANDS`: two or more vertically stacked meaning regions;
- `NESTED_INFO_BOARD`: a framed information surface containing a dominant visual and supporting evidence;
- `PROCESS_STRIP_PLUS_RESULT`: a process strip followed by a larger result field;
- `CENTRAL_OBJECT_ORBIT`: one central object with product-bound evidence around it;
- `FRAMED_LIFESTYLE_PLUS_CARDS`: one large framed scene plus several smaller scenario/result panels;
- `PRODUCT_PAIR_PLUS_TABLE`: product views or variants above a structured specification surface;
- `MACRO_DIAGRAM`: a dominant material or mechanism macro with inset or directional relation;
- `PORTRAIT_STRIP_PLUS_PAIN_ARRAY`: people or contexts in a strip followed by visible pain states;
- `ASYMMETRIC_EDITORIAL`: unequal editorial regions with one decisive visual anchor;
- `COMPARISON_SPLIT`: truthfully controlled side-by-side conditions;
- `SELECTION_MATRIX`: buyer condition mapped to product or option;
- `SPECIFICATION_BOARD`: structured information connected to an actual product or use consequence.

Do not treat floating generic cards over a single photograph as a multi-zone structure. A secondary panel must carry a distinct object, action, state, result, evidence source or buyer decision job.

## Campaign rhythm

Derive campaign rhythm directly from the ordered module blueprints; do not maintain a duplicated `spatial_structure_rhythm` ledger.

- A normal campaign may not plan more than one consecutive `FULL_BLEED_SINGLE_SCENE`.
- A campaign of eight or more modules normally needs at least four observed structure families; six or seven needs at least three; shorter campaigns need at least two when they contain more than one module.
- Do not maximize novelty for its own sake. Eight screens normally use four or five families; excessive one-off structures are rejected when structure diversity starts driving the story.
- Every rolling three-module window must contain at least two structural relationships unless a documented same-condition comparison requires repetition.
- Changing the subject, background, crop, color or copy position does not count as a structural change.
- The plan must state why each module's structure performs its buyer job and how it differs visibly from its neighbor.
- `COMPARISON_SPLIT` requires a truthful `comparison_basis`; a purchase-closure module cannot use `PRODUCT_PAIR_PLUS_TABLE` or `SPECIFICATION_BOARD` as its final visual form.

## Prompt compilation

Compile geometry into visible consequences. Do not merely send an enum name.

For example:

> Build two vertically stacked meaning regions, not one full-bleed photograph. The upper region contains three connected process panels and occupies about 42 percent of the canvas. The lower region is one larger result scene occupying about 58 percent. The boundary is visually clear while the action path continues across it.

The compiled prompt must explicitly state when a full-bleed single scene is forbidden. It must also name the minimum zones, dominant regions, secondary panels and mini-scenes.

## Series-master boundary

The accepted first screen may transfer palette roles, light, photographic finish, typography character, graphic language, product rendering and recurring motifs. It may never transfer layout skeleton, subject coordinates, product occupancy, text zone, camera scale, zone geometry or the full-bleed poster template.

When the current structure differs from M01, the prompt must say that the first-screen reference is style-only. If the provider cannot respect that distinction, omit the series master rather than sacrificing the approved spatial structure; keep continuity through the written passport and verified product sources.

## Pixel audit

Observe and record:

- `structure_family_observed`;
- `structural_zone_count`;
- `structural_relationship_observed`;
- `full_bleed_single_scene`;
- `dominant_zone_count_observed`;
- `secondary_panel_count_observed`;
- `mini_scene_count_observed`;
- `previous_structure_similarity`;
- `same_full_bleed_template_as_previous`;
- one concrete `spatial_structure_observation`.

Hard-fail a module when the planned split, stack, nested, orbit, process, matrix or multi-scene structure appears as one full-bleed photo; when required regions are absent; or when it repeats the preceding full-bleed template despite a planned structural change.


