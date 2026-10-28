# Exact Text Replacement

Use deterministic text rendering when the replacement string must be exact and a compatible local font is available.

## Workflow

1. Start every revision from the normalized source.
2. Separate the operation into:
   - clean-background reconstruction;
   - typography extraction;
   - full-run layout;
   - exact-selection compositing.
3. Before rendering, create a measurement record from the normalized original. Include:
   - the complete logical text run: family, weight, size, baseline, tracking, alignment, line count, alpha bounds, fill, outline, shadow, and antialiasing;
   - the container: bounds, corner radius, fill model, gradient direction and sampled stops;
   - the signed typography offsets relative to the container, including deliberate top, side, or bottom overflow;
   - the sample locations or repeated untouched evidence used for every color role.
4. Match candidate fonts against the complete original run, not one glyph or a reused substring. Compare grayscale or alpha contours so antialiasing and stroke weight affect the fit; a binary core mask alone can favor an incorrectly bold font.
5. For variable fonts, search the relevant weight axis instead of assuming regular or bold. Render without synthetic stroke first; add same-color stroke only when measured evidence shows the original has one.
6. Sample each color role independently from its corresponding original pixels or repeated untouched evidence. Keep fill, outline, shadow, glow, and every container/background gradient stop separate. Do not reuse a nearby background value as the outline merely because the colors look similar. When a role has insufficient evidence, preserve original pixels where possible and record the uncertainty instead of inventing a visually plausible value.
7. Remove the complete original glyph footprint, including its antialias halo, before drawing the replacement. Cleaning only the saturated core can leave a ghost.
8. Treat the user selection as maximum authority, not the cleanup target. Reconstruct the smallest region that contains the old glyph footprint.
9. Rebuild a broad rectangular band only when untouched pixels prove the underlying surface is continuous across it. If the selection touches a badge, border, rounded corner, silhouette, highlight, shadow, gradient boundary, or texture transition, preserve those original pixels and repair only the measured glyph footprint.
10. Typeset the complete replacement string as one logical run. Do not preserve a shared prefix or suffix while rerendering the rest unless font family, weight, size, baseline, tracking, antialiasing, and color are proven identical and a magnified seam review passes.
11. Preserve the original text bounds, baseline, visual center, line count, alignment, tracking, and four inner margins where possible. Do not infer the text box from the full user selection.
12. Build the clean background/container and typography as separate layers with independent geometry. Reproduce the measured relationship between their envelopes, including intentional text overflow. Never enlarge, move, or reshape the container merely to contain repositioned text. Before compositing, verify the complete glyph alpha bounds, side bearings, and selection fit so the first or last glyph is not clipped.
13. Change one measured variable per revision when responding to typography feedback.
14. Hard-lock outside the exact selection and verify `outside_changed_pixels == 0`.

## Typography consistency

Render the replacement as one logical run and one typography layer.

- Use one font file or family, variable-font axis set, nominal size, weight, stroke model, baseline, tracking rule, fill, and antialiasing method for every character.
- Never preserve a shared character from the source and join it to newly rendered characters by default. A visually similar prefix can still differ in rasterization, scale, baseline, or edge treatment.
- Never render characters separately and resize them independently to fill available space. Surface perspective may change apparent widths across the run, but it must be one continuous transform applied to the complete run.
- Sample text color from the original glyph pixels before cleaning them. Sampling after background reconstruction can silently turn the replacement text into the container color.
- Validate the complete alpha bounds, including stroke, shadow, glow, and subpixel antialiasing. Nominal font boxes are not reliable delivery bounds.

## Surface and perspective fitting

Infer the transform class from the original run and its physical surface before fitting parameters.

1. Measure the original baseline, vertical strokes, text envelope, and container axis.
2. Determine whether the observed deformation is horizontal wrapping, vertical curvature, planar perspective, shear, rotation, or a combination.
3. Render the complete original reference run with candidate fonts, weights, sizes, tracking, and stroke settings.
4. Apply candidate surface transforms and compare grayscale or alpha contours against the complete original run.
5. Fit the smallest transform family supported by measured evidence. Do not use a more flexible warp merely because it can lower error.
6. Apply the accepted typography settings and the same continuous surface transform once to the complete replacement run.

For text printed around a mostly vertical bottle or can:

- Model cylindrical wrapping primarily as a left-to-right coordinate mapping or column-wise horizontal scale.
- Keep the baseline straight when the original baseline is straight.
- Do not introduce an up/down arch, sinusoidal bend, or per-character vertical offset to imitate horizontal wrapping.
- Do not choose curvature by eye. Search bounded parameters and record the contour mismatch score against the original text.
- Do not use the warp to hide an incorrect font, weight, size, or tracking choice. Fit typography and surface geometry as separate recorded parameter groups.

Record the transform family, axis, parameters, search bounds, fit score, accepted text alpha bounds, and evidence used to reject other transform classes.

## Content-fit preflight

Perform this check on the uncomposited, fully rendered replacement layer:

```text
overflow = replacement_alpha AND NOT authority_selection
overflow_pixels = count(overflow)
```

Use every nonzero alpha value by default so faint antialiasing is not discarded.

- Require `overflow_pixels == 0` before strict compositing.
- Treat nonzero overflow as an authority or layout conflict, not as a compositing defect.
- Do not let the hard lock silently crop the first or last glyph.
- Do not automatically shrink, horizontally compress, move, curve, or distort the text solely to make the metric pass.
- Preserve the visually correct full-run candidate and measure the exact overflow bbox and alpha mass.
- In interactive mode, request a larger selection.
- In a pre-authorized autonomous deployment, use the safe-expansion policy in `autonomous-deployment.md`.
- If layout adaptation is allowed by product policy, enforce explicit tolerances and re-run the complete typography and perspective review; never adapt one glyph independently.

For acceptance, also confirm:

```text
content_alpha_outside_authority == 0
accepted_candidate_vs_final_inside_authority == 0
```

The second metric prevents a visually approved complete glyph from being replaced by a later clipped or reprocessed version.

## Protected structures inside the selection

The authority selection may overlap nearby content that the request does not authorize semantically, such as an upper slogan, label edge, rounded corner, highlight, envelope tip, or adjacent logo.

1. Identify these structures from the normalized source before cleanup.
2. Create a separate `protected_inside_selection` mask or explicit measured bands.
3. Restrict glyph detection and cleanup to the measured original text footprint and its antialias halo.
4. Hard-restore protected pixels from the source after cleanup, text rendering, blur, and compositing.
5. Require `protected_changed_pixels == 0` when exact preservation is intended.

Do not classify every pixel with a similar hue as old text. Combine color evidence with the measured text band, connected shape, original run bounds, and protected masks. A broad red threshold can erase red artwork or turn warm product/background pixels into a flat container fill.

## Acceptance

1. Review a magnified crop of the original, measurement overlay, clean background layer, typography layer, candidate, and current best result.
2. Confirm that the entire replacement run has one consistent family, weight, size, baseline, tracking, color, and antialias treatment.
3. Inspect selected-but-unaffected container pixels, especially borders, curves, gradients, shadows, and highlights. Outside-selection equality cannot detect damage inside the authorized selection.
4. Compare the candidate with the current best before promotion. Keep a worse candidate diagnostic-only.
5. After visual acceptance, promote that exact candidate without another feather, fill, color, or texture pass.
6. Verify both:
   - source versus final outside the selection: zero changed pixels;
   - accepted candidate versus active final inside the selection: zero unexpected changes.

## Calibration

Record the fitted font file or family, variable-font axes, size, stroke width, color roles, baseline, tracking, placement, fit method, and verification results with the task artifact. Treat acceptance as evidence for the current image, not as a universal default.

## Failure patterns

- Partial-run replacement can create a visible word-space, weight break, baseline seam, or antialias mismatch even when the replacement substring is correct.
- Copying glyphs from another label can inherit a different size, tracking, rasterization, or local treatment. Use another label only as supporting evidence, not as the default source for replacement glyph pixels.
- Repainting a whole badge interior can create a cutout, flat patch, or damaged edge even when the sampled average color is close.
- Coupling typography placement to container regeneration can silently enlarge the container when text is moved. Treat their geometry and transforms independently.
- Collapsing a gradient background and text outline into one sampled color can erase their original contrast even when both appear to belong to the same color family.
- Rendering on a canvas sized only from the nominal text box can clip an outer glyph or its antialias/stroke envelope. Validate the complete alpha bounds before compositing.
- Fitting a horizontal bottle wrap with vertical bending can make the baseline arc, change apparent character sizes, and detach the text from the printed surface.
- Per-character transforms can make one glyph larger, heavier, higher, or sharper than its neighbors even when the full string is correct.
- A strict composite can report zero outside changes while silently cropping content whose alpha extended beyond the authority mask. Run the content-fit preflight before compositing.
- Broad color thresholds can mistake adjacent red artwork, warm product color, or envelope geometry for old glyphs. Restrict cleanup to the measured glyph band and preserve protected structures explicitly.
- A successful outside-selection lock can coexist with a visibly damaged background inside the selection. Always perform local visual review.
- Promoting before review can replace a better active result with a failed experiment. Generate and inspect a preview first.
