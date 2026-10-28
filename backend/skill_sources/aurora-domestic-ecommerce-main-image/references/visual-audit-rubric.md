# Visual audit rubric

v0.1.17 additions: judge Campaign Constitution continuity, source-background
leakage, selling-point evidence, and claim-to-evidence clarity from actual
pixels. One page may carry several coordinated selling points. Audit their
hierarchy and evidence mapping; never fail a page merely because it contains
multiple claims, information bars, specifications, trust facts or commercial
closure. Record a complete inventory of material repair-triggering defects
before the sole automatic repair; keep minor issues as advisory warnings.

Judge the actual image pixels after every generation. Do not infer a pass from
the prompt, JSON, file existence, dimensions, hash, or generation receipt.

The Agent owns this judgment. Do not ask the user to decide routine product
proportion, commercial density, typography, pointer accuracy, page role or
thumbnail quality. Submit `AGENT_DIRECT_PIXEL_REVIEW` provenance and a concrete
decision summary. Generic statements such as “looks good” are not evidence.

Before scoring, record `performance_observation`: estimated product occupancy,
headline visual rank, physical evidence actually seen, number of credible depth
layers, whether a commercial closure is visible, background competition, and
the thumbnail scan order. These observations must describe pixels rather than
repeat the prompt.

Also record `plan_fidelity_observation` by describing the visible subject/view,
scene geometry, composition, scan order, missing required elements and any
unapproved plan changes. Record `selling_point_proof_observation` by covering
the text and naming the remaining pixel evidence. For a multi-claim page,
inspect the lead evidence and planned supporting evidence: a main action may
prove one claim while non-equal macro, sequence, comparison or result regions
prove the others. A renderable lead claim that only survives as text is a
material failure. A weak supporting proof is advisory unless the page's stated
purchase task depends on it or the page becomes text-only overall.

Do not use “too many selling points” as a defect. The defect is unclear
hierarchy, text replacing proof, or the same selling-point bundle and conclusion
being repeated across two or three pages. Useful verified information must not
be deleted solely to simplify the count.

Record observed pixel evidence for every check:

- `product_identity_match`: silhouette, proportions, color, components, labels,
  attachment and relative positions match current-product evidence.
- `factual_copy_match`: visible claims, quantities, conditions, labels, and
  offers remain fact-bound and readable.
- `typography_style_match`: font roles, treatment family, emphasis, and numeric
  styling remain inside the frozen Campaign Style.
- `typography_legibility`: Chinese is not garbled; hierarchy, line breaks,
  numeric units, and thumbnail readability succeed.
- `page_role_expression`: the page performs its assigned purchase task.
- `evidence_relation_visible`: the claimed component, state, subject, or action
  relation is actually visible.
- `campaign_style_match`: typography roles, palette roles, graphic language,
  information surfaces, lighting, and photography finish remain in family.
- For `campaign_constitution_match`, compare the full `product_identity_lock`
  and `background_style_lock`: page-to-page scene, light angle, tonal emphasis
  and depth changes are allowed. Fail only when product identity changes or the
  page visibly leaves the campaign's palette roles, material/graphic language,
  typography character and photography finish.
- `layout_not_cloned`: the page does not repeat an accepted page's shot key,
  skeleton, reading path, or evidence topology.
- `visual_performance_match`: the page satisfies its compiled product,
  headline, physical-evidence, depth, closure and visual-tension contract.
- `baseline_performance_match`: the page reaches the transferable baseline in
  `validated-commercial-baseline-v0.1.16.md` without copying a prior product's
  identity, copy or layout.
- `approved_plan_fidelity_match`: the generated pixels preserve the approved
  page duty, lead selling-point meaning, buyer takeaway, product identity and
  necessary commercial condition. A stronger camera, composition, scale,
  lighting treatment, copy phrasing or visual metaphor is not plan drift.
- `selling_point_visible_without_copy`: the image communicates the lead benefit
  direction through product, context, material, action, result, comparison,
  sensory atmosphere or credible visual metaphor; it need not reproduce the
  user's sentence word for word without text.
- `text_not_substituting_for_visual_proof`: the canvas is not merely a typed
  checklist. Text may accurately complete parameters and invisible facts.
- `thumbnail_success`: product and primary message survive a fast small-scale
  scan.
- `no_unsupported_claim`: pixels do not imply an unsupported result, mechanism,
  certification, accessory, offer, or authority.
- `no_hard_error`: no identity drift, product occlusion, unreadable text,
  impossible geometry, detached component, or factual hallucination.

Use `STYLE_LAYER_ONLY` only when failures are limited to typography,
Campaign-style execution, or information-surface treatment. Use
`LAYOUT_OR_COMPOSITION_REPAIR_REQUIRED` for cloned layouts, failed page roles,
or thumbnail composition. Use `PRODUCT_OR_FACT_REPAIR_REQUIRED` for identity,
fact, evidence, geometry, legibility, or unsupported-claim failures. Preserve
passed layers during targeted repair.

Before checklist scoring ask one outcome-first question: at thumbnail size,
does this image make the product benefit feel clear, valuable and desirable?
If no, identify the missing visual tension or benefit amplification. If yes and
facts/identity are intact, do not fail the image merely because execution
differs from the plan sketch or omits an optional supporting point.

## Deterministic repair decisions

- Identity, impossible geometry or unsupported facts: reject the complete page
  and restart from original same-product evidence.
- Unnatural view caused by forcing every control label to be readable: prefer a
  natural front or supported three-quarter view; prove controls on a later
  detail page.
- Pointer or callout target is uncertain: remove it. Do not repeatedly move a
  decorative pointer around an unverified target.
- Search cover reads as an airy poster: rebuild the page role with a dominant
  complete product, one hook, zero to three short modules and a specification,
  service or transaction closure.
- Style-only drift: retain product, role, evidence topology and composition;
  repair only typography, palette, information surfaces or photography finish.
- Layout failure: retain passed identity and facts, but replace the shot,
  subject anchor, reading path and evidence topology rather than moving text.

Trigger the sole automatic repair only for a material defect: product identity
or factual drift, unsupported claims, hard geometry/text errors, Campaign or
source-background leakage, an unrecognizable page role, a materially broken
approved plan, or missing visual proof for the page's core renderable claim.
Scores below 70 on material checks or an overall mean below 72 block. Scores
from 70 upward and minor styling/composition preferences are advisory; retain a
good image and continue. A failed page receives exactly one scoped repair and
is re-inspected before progression.
