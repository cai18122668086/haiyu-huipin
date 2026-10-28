---
name: masked-image-repair
description: Edit or repair only an authorized region of an existing raster image while preserving every unauthorized pixel exactly. Use for box-selected, brushed, masked, inpainted, removed, replaced, recolored, text-corrected, texture-repaired, packaging or curved-surface typography, autonomous website edits with pre-authorized safe mask expansion, GPT Image 2 edits, and any task where product geometry, identity, background, borders, text, logos, watermarks, crop, or composition outside the authority mask must remain pixel-locked.
---

# Masked Image Repair

Treat the normalized source as the only canvas and the finalized authority selection as the only permission to change pixels.

## Non-negotiable invariant

Require the delivered result to satisfy:

```text
result[p] == source[p] for every pixel p outside authority_selection
```

In the default interactive mode, `authority_selection` is the user's exact original selection. In a non-interactive deployment, it may be an `effective_selection` produced before editing by an explicit, pre-authorized safe-expansion policy; retain the original user selection and audit every added pixel. Never treat padding, model context, feathering, model output, antialiasing, or texture reconstruction as authority.

## Load references

- Read [references/prompt-protocol.md](references/prompt-protocol.md) before mask conversion, model/API editing, compositing, retries, or acceptance review.
- Read [references/performance.md](references/performance.md) when choosing between deterministic repair and a model call, or when generation speed matters.
- Read [references/text-replacement.md](references/text-replacement.md) for exact text replacement, typography matching, label or badge cleanup, and text-specific acceptance review.
- Read [references/autonomous-deployment.md](references/autonomous-deployment.md) for one-submit website or agent pipelines that cannot ask the user to revise a selection.

## Required inputs

Require one source image, one explicit selection, and one concrete edit instruction. Proceed when all three are unambiguous.

## Preflight selection geometry

Before any model call, verify that the selection can contain the requested new silhouette and does not intersect identity-bearing regions discontinuously.

- For hair, clothing, limbs, faces, text, logos, and other structured subjects, prefer a selection that either excludes a protected structure completely or covers the intended replacement continuously.
- Warn or request a new selection before generation when strict compositing would splice together old and new versions of a face, body part, object edge, or text.
- For a deterministic insert or text layer, calculate `overflow_pixels = content_alpha AND NOT authority_selection` before compositing. A nonzero count is a preflight failure, not permission to clip, squeeze, shift, or distort the content.
- Use `scripts/preflight_content_fit.py` for deterministic alpha-fit measurement and audited autonomous expansion.
- In interactive mode, request a revised selection. In pre-authorized autonomous mode, follow the safe-expansion decision tree in `references/autonomous-deployment.md`.
- Treat a broad selection as maximum authority, not proof that every selected pixel should change.

## Choose the execution path

1. Use deterministic local repair for flat colors, gradients, repeating dots/stripes/grids, simple geometric fills, and exact text overlays when the required typeface or a usable text layer exists.
2. Use GPT Image 2 for organic texture, lighting, anatomy, object synthesis, difficult occlusion, or content that cannot be reconstructed reliably.
3. Use a hybrid path when the background is deterministic but the inserted subject is generative: reconstruct the background first, generate only the complex insert, then compose in original coordinates.

Prefer the lowest-cost path that can meet the visual requirement and the pixel-lock invariant.

## Core workflow

1. Normalize EXIF orientation and convert the source to same-size RGBA PNG.
2. Convert the user's selection to a binary authority mask: white is editable, black is locked.
3. Optionally create a separate expanded context mask for model guidance.
4. Keep these masks distinct:
   - `selection.png`: exact authority for final delivery.
   - `mask_api.png`: optional expanded model context.
5. In autonomous mode, also retain `user_selection.png`, `effective_selection.png`, and the expansion audit; use only the effective selection as final authority.
6. Always start every revision from the normalized original, never from a previously edited result.
7. Build the background/content in original-image coordinates.
8. For patterned backgrounds with text, reconstruct the clean background first and overlay the new text second.
9. Run the content-alpha fit gate before final compositing.
10. Apply inward-only feathering by multiplying any blurred mask by the exact authority selection.
11. After all blur, antialiasing, text, template copying, resizing, and compositing, hard-lock the result with the exact authority selection one final time.
12. Run `scripts/verify_selection_lock.py`. Reject the result unless `outside_changed_pixels` is `0`.
13. Save one raw/model artifact when applicable, one clean intermediate when diagnostically useful, and one active delivery result. Label intermediates clearly.

## Artifact states and version integrity

Keep model generation and deterministic post-processing as separate state machines.

1. Save every model response immediately as an immutable raw artifact with a generation ID, hash, dimensions, prompt, and visual-review status.
2. Review the raw artifact before compositing. Record `raw_visual_success` when the requested edit is visibly successful, even if the artifact is not pixel-locked.
3. Derive every locked candidate from the normalized source plus that immutable raw artifact. Never overwrite the raw artifact.
4. Record mask, semantic-mask, feather, or compositing experiments as post-processing attempts, not as new model generations or user-facing revisions.
5. Do not replace the active successful visual candidate with a failed locked candidate. Promote a candidate to the active strict result only after both visual review and pixel-lock verification pass.
6. Never duplicate one raw image under multiple generation revisions. Reuse its generation ID and attach multiple post-processing attempt IDs.
7. If the raw artifact is visually successful but strict compositing fails, report both facts explicitly: `raw_visual_success` and `strict_lock_failed`. Keep the raw artifact available for inspection or user selection.
8. Do not mark the whole task as having no successful version while a visually successful raw artifact exists. Use a compound status such as `raw_visual_success_locked_failed` or `needs_reselection_for_strict_lock`.
9. If the user explicitly relaxes pixel-lock requirements, a raw artifact may be delivered only as a clearly labeled visual-only result, together with the measured outside-selection change count or ratio.

## Visual acceptance and stop rule

Judge candidates against the user's explicit request and observable defects, not hypothetical pixel differences inside the editable region.

1. Treat instructions such as "preserve the design, shape, layout, or identity" as visual constraints. Reject only clear, visible structural drift that matters to the request.
2. When the first raw generation visibly satisfies the request, mark `raw_visual_success` and stop model generation.
3. Apply only the smallest deterministic operation required for strict delivery. Do not add semantic segmentation, recoloring, regeneration, or heuristic reconstruction to fix a merely suspected defect.
4. Before every retry or post-processing attempt, name one observable defect and the single action expected to improve it.
5. Compare every processed candidate with the current best visual candidate. If it is visibly worse, keep it diagnostic-only and preserve the better candidate as active.
6. If strict pixel locking conflicts with the successful raw appearance, stop instead of stacking repairs. Preserve the raw artifact and report the dual outcome. Ask for a revised selection in interactive mode; use the declared fail-safe policy in autonomous mode.

## Deterministic compositing

Use `scripts/strict_composite.py` for the final gate:

```powershell
python scripts/strict_composite.py `
  --source source.png `
  --generated generated.png `
  --selection selection.png `
  --out result_locked.png `
  --feather 6
```

Feathering is inward-only. The script restores original pixels outside the exact selection and verifies the invariant before returning success.

## Pattern and text repairs

For regular dots, stripes, grids, or tiled texture:

1. Estimate the lattice, spacing, phase, shape, color, and local surface from untouched pixels in the original image.
2. Reconstruct only within the authority selection in original coordinates.
3. Preserve original pixels near the selection boundary when possible; transition only inward.
4. Restore pattern elements hidden by the old content.
5. Overlay exact new text or artwork after the clean background exists.
6. Apply the exact selection again after text antialiasing; blur can otherwise leak pixels outside the mask.

Do not resize and paste a full-frame generated background into the source. Small geometry and lattice differences create cutout seams, ghost dots, and phase mismatch.

## Failure modes to prevent

- Never use the expanded API mask as the delivery mask.
- Never expand authority silently or without an explicit interactive approval or pre-authorized autonomous policy.
- Never blur a mask and use it without multiplying it by the exact selection afterward.
- Never let a copied texture patch, dot template, shadow, or text alpha extend outside the selection.
- Never clip a complete glyph, object edge, shadow, or antialias halo merely to force it through the pixel-lock gate.
- Never compensate for an undersized selection with unapproved typography compression, arbitrary repositioning, or geometry distortion.
- Never regenerate the whole image merely to fix local text or a regular texture.
- Never build a later revision from an earlier generated revision; drift accumulates.
- Never splice preserved and rerendered glyphs within one logical text run by default. Fit the original run and typeset the complete replacement together unless identical typography metrics are proven.
- Never describe visually similar pixels as “locked.” Verify exact equality.
- Never expose multiple unlabeled background intermediates as competing final results.
- Never call a failed local composite a failed model generation.
- Never hide, overwrite, or demote a visually successful raw generation merely because a later strict composite failed.
- Never create revision clutter by saving the same raw generation repeatedly for each local mask experiment.

## Acceptance gate

Accept only when:

1. Canvas dimensions match the normalized source.
2. Outside-selection changed pixel count is exactly zero.
3. The requested local change is present.
4. Pattern phase, geometry, texture, lighting, and occlusion are continuous at the boundary.
5. Text is exact and added after background reconstruction.
6. The complete accepted content alpha lies inside the finalized authority selection.
7. The promoted final is pixel-identical to the visually accepted candidate inside the selection.
8. No unintended border, shadow, collage, label, watermark, or whole-image redesign appears.

Keep raw generation for diagnosis, but deliver only the strictly locked result by default. When strict delivery fails but the raw generation succeeds visually, preserve and disclose the raw candidate instead of claiming that no successful version exists.
