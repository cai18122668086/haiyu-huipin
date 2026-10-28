# Autonomous Deployment

Use this policy for one-submit websites or agents that cannot ask follow-up questions.

## Authorization contract

Do not reinterpret an ordinary user selection as permission to expand it. The product must disclose before submission that it may make a minimal automatic expansion within the same verified editable surface to preserve complete edges, shadows, and antialiasing.

Support two execution modes:

- `interactive`: the exact user selection is final authority; request a new selection when content overflows.
- `autonomous`: a published product policy pre-authorizes bounded safe expansion before editing.

Keep these masks separate:

- `user_selection.png`: immutable record of the user's target.
- `safe_region.png`: detected container or surface inside which expansion is permitted; it is a constraint, not authority.
- `effective_selection.png`: finalized authority after the autonomous preflight.
- `mask_api.png`: optional model context; never use it as authority.

For compatibility with existing compositing scripts, pass `effective_selection.png` as `selection.png` only after retaining and hashing both the user and effective masks.

## Decision tree

1. Build the complete prospective content layer from the normalized source.
2. Calculate its full alpha silhouette, including antialiasing, stroke, shadow, and glow.
3. Measure:

```text
overflow = content_alpha AND NOT user_selection
overflow_pixels = count(overflow)
```

4. If `overflow_pixels == 0`, set `effective_selection = user_selection`.
5. If overflow exists, test automatic expansion.
6. If expansion is unsafe, try only product-authorized layout adaptations within declared tolerances.
7. If no safe candidate passes, return a controlled failure instead of clipping content or changing unauthorized pixels.

Run the deterministic gate with:

```powershell
python scripts/preflight_content_fit.py `
  --selection user_selection.png `
  --content complete_content_layer.png `
  --safe-region safe_region.png `
  --source source.png `
  --halo 1 `
  --max-added-pixels BUDGET `
  --allow-expand `
  --effective-out effective_selection.png `
  --overlay-out expansion_overlay.png
```

Treat a nonzero exit as a blocked candidate. Do not continue to strict compositing.

## Safe expansion

Permit automatic expansion only when all conditions pass:

1. Every overflow pixel and its required antialias halo lie inside `safe_region`.
2. The added region does not cross a container edge, rounded corner, occlusion, seam, identity-bearing feature, adjacent text, logo, label outline, or protected artwork.
3. The added pixel count, distance from the user selection, and relative expansion are within scale-aware product budgets.
4. The expansion is derived from the measured content silhouette, not a blind rectangular or morphological growth of the whole selection.
5. The final candidate still preserves the container, texture, lighting, and neighboring structures on magnified review.

Construct:

```text
required_halo = dilate(content_alpha, antialias_radius)
added_authority = required_halo AND NOT user_selection AND safe_region
effective_selection = user_selection OR added_authority
```

Reject expansion when any required halo pixel lies outside `safe_region`.

Use a scale-aware budget based on local content height or container size, with an absolute cap. Store the configured budget with the task; do not embed one universal pixel count that behaves differently at different resolutions.

## Layout adaptation

Use layout adaptation only when the product contract explicitly allows it.

- Preserve the complete logical run or object.
- Change one measured global variable per attempt.
- Set tolerances for font size, horizontal scale, tracking, baseline shift, and geometric transform.
- Do not change one glyph independently.
- Do not introduce a new curvature class to make content fit.
- Compare every adapted candidate with the original visually correct candidate. Reject a candidate that solves overflow by visibly degrading typography or surface fit.

Prefer minimal safe expansion over typography distortion when the overflow belongs to the intended content and remains on the same safe surface.

## Internal preview and promotion

No conversational UI does not remove the preview gate.

1. Generate a labeled candidate from the normalized source.
2. Run deterministic geometry, content-fit, protected-structure, and pixel-lock checks.
3. Inspect or score a magnified comparison of source, clean background, content layer, candidate, and current best.
4. Promote only the accepted candidate.
5. Verify that the delivered result is pixel-identical to the accepted candidate inside the effective selection.

Do not write over the active result while a candidate is still under review.

## Fail-safe behavior

A one-submit product can still fail safely without conversational follow-up.

- Return a structured status such as `needs_larger_selection`, `unsafe_expansion`, or `layout_out_of_tolerance`.
- Preserve the normalized source and the best uncomposited visual candidate for diagnosis.
- Do not publish a clipped or structurally damaged image.
- If the product must return an image, return the unchanged source with the failure status rather than an unauthorized edit.

## Audit record

Record at least:

- execution mode and policy version;
- source, user-selection, safe-region, and effective-selection hashes;
- user and effective selection bboxes;
- overflow pixel count, bbox, and alpha mass;
- added-authority pixel count and maximum expansion distance;
- protected-structure intersection count;
- any layout adaptations and their tolerances;
- `outside_effective_selection_changed_pixels`;
- `changed_outside_user_selection`;
- accepted-candidate versus delivered-result difference counts.

Require:

```text
content_alpha_outside_effective_selection == 0
outside_effective_selection_changed_pixels == 0
accepted_candidate_vs_delivered_inside_effective_selection == 0
```
