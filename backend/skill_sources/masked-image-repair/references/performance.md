# Performance Strategy

## Fastest-path decision

Use the first viable path:

1. **Local deterministic repair** — flat/gradient backgrounds, regular patterns, simple fills, and exact text. Usually seconds and no model call.
2. **Hybrid repair** — reconstruct deterministic background locally, call the model only for the complex insert.
3. **Cropped model edit** — send the selected region plus limited context instead of the full image, then map it back and apply the exact authority mask.
4. **Full-canvas model edit** — reserve for edits whose lighting, perspective, or structure genuinely requires the entire frame.

## Model-call optimization

- Use one model call per defect class.
- Use `quality=low` for a quick composition draft; use `medium` for most local edits and `high` only for the accepted final when detail requires it.
- Request the smallest valid output size. Larger images and high quality increase latency.
- For a cropped edit, include enough context for edges and lighting, align crop edges to multiples of 16, and meet the model's minimum pixel count by controlled scaling. Map the result back before strict compositing.
- Cache normalized source PNG, exact selection, API mask, prompt, and crop transform. Reuse them for unchanged retries.
- Reuse HTTP connections and set one bounded retry for transient failures.
- Keep raw model output for diagnosis instead of rerunning merely to inspect it.

## Avoid slow loops

- Do not generate a whole clean background when a regular pattern can be inferred locally.
- Do not ask the model to render exact text when a deterministic text layer is available.
- Do not run “background repair” and “text restore” as separate model calls when the first step can be local.
- Do not repeatedly increase quality to solve a mask/compositing defect; fix the deterministic gate instead.
- Do not regenerate from the last result. Always return to the original and reuse cached inputs.
- Stop model calls once a raw candidate visibly satisfies the user's request.
- Do not add heuristic segmentation, recoloring, or reconstruction for an unmeasured concern. Require one named visible defect, one targeted action, and a comparison against the current best candidate.

## Practical modes

Offer three user-facing modes:

- **Fast preview:** deterministic path when possible; otherwise cropped `quality=low`.
- **Balanced:** deterministic/hybrid path; otherwise cropped or full `quality=medium`.
- **Final quality:** run only after preview approval; use `quality=high` when visible detail justifies it.

The largest speed gain comes from avoiding unnecessary model calls. Mask processing and strict compositing should normally take seconds; model generation is the dominant latency.
