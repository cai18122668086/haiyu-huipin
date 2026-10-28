# Continuous session and repair policy v0.1.17

The approved campaign remains active from planning through finalization and all
later user corrections. Every image call must originate from `generate-next`
and use the returned concise prompt. Keep the full Campaign Constitution,
current page contract, source-scene isolation state and user delta in the
request for execution/audit memory; do not paste the raw structures into the
image-model prompt.

The Campaign Constitution must carry the full product identity lock and the
frozen background style lock, not hashes alone. Rehydrate both on every initial
generation, repair and user adjustment. Vary the shot and scene content by page
duty while keeping the same product and the same visual world.

The user has two planned checkpoints only: approve the six-page plan, then
review the complete six-image set. All product information actively supplied by
the user is `USER_PROVIDED_DISPLAY_FACT` and is used without asking for a second
verification. A claim added by the model is not user-provided; remove it during
the single automatic repair instead of asking whether it might be true.

## User correction

A conversational correction is not an image prompt. Convert it verbatim to
`schemas/user-adjustment.schema.json`, run `apply-user-adjustment`, then use the
returned state and `generate-next`. The delta may change only the named page and
named visible details. Product identity, user-provided display facts, campaign-wide style,
page role and background isolation remain immutable. Unmentioned composition
notes and proof suggestions are not pixel locks; the Agent still chooses the
strongest visual execution. A global change requires replan or restart.

This path also applies after `FINALIZED`: reopen the named page in the same
session, supersede only its accepted asset and prior manifest, keep the other
five accepted pages, generate once, audit, rerun the six-page role gate, and
finalize a new manifest.

## Repair budget

The first materially failed image must produce one complete deficiency
inventory. Every repair-triggering check needs a visible problem, root cause
and required change; advisory shortcomings do not spend the repair. The repair
plan must address all of them together, state what passed and must remain fixed,
and describe one coherent second-attempt strategy. Exactly one automatic repair
is permitted. The automatic repair must edit the failed first draft as
`REPAIR_CANVAS_ONLY`; original photographs remain identity references and their
full backgrounds are forbidden as the second-attempt canvas. If the second
attempt still has defects, record all residual issues, select that result for
the final set, and continue the remaining pages without interrupting the user.
The user sees the complete set and residual disclosure only at final review.

## Selling-point expression

Evidence being present is insufficient, but expression is not a forensic test.
Translate the lead benefit into one strong product-centered visual idea using
real material/component/action/state/result, credible context, comparison,
sensory atmosphere or controlled commercial metaphor. Camera, scale, light and
space are creative choices, not fixed percentages. With copy hidden, the buyer
should still feel the lead benefit direction; exact numbers, conditions and
invisible mechanisms may rely on accurate text. Related support may share the
hero story and may be omitted when it weakens the image.

## Source-background isolation

Source imagery supplies product identity and true structure, not scene design.
Unless the user explicitly preserves a named element, original desks, walls,
seams, clutter, keyboards and capture edges are noise. This rule survives every
repair and user adjustment. On automatic repair, the first generated commercial
canvas is the edit target; source images are supporting identity references
only. Any leakage fails `source_background_noise_absent`.
