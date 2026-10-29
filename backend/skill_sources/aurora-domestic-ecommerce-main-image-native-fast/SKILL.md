---
name: aurora-domestic-ecommerce-main-image-native-fast
description: Compile and deliver one domestic search cover plus five carousel images using the original Aurora v2 compiler, identity lock and Campaign Style, without blocking visual audits, calibration or automatic repairs.
---

# 国内主图·原生精简

This independent version derives from the user's original Aurora v2 archive.
Use `scripts/harness.py` as the only public runtime entry. Original compiler,
style selection, identity validation and design references remain byte-exact.
The website owns image dispatch, durable receipts, bounded rolling concurrency,
source ownership and SHA checks, recovery, credits and original-file downloads.
The original workflow and the separate one-pass planning fast version are retained.

## Preserved design and factual authority

Use the original same-product images as product-identity authority and explicit
user facts as commercial authority. Retain the exact silhouette, proportions,
components, colors, packaging and visible identity. Source-photo environments
are capture noise. Never use generated images as product evidence or invent
numbers, certifications, offers, product structure or commercial claims.

Read references/six-page-strategy.md, references/visual-strategy.md and
references/commercial-performance.md for the original visual design contract.
The copied reference documents also describe historical audit procedures; this
version's execution instructions below take precedence for runtime operations.

Keep all six ordered duties: search cover, identity hero, core-feature proof,
scene/operation, detail/trust and offer/specification closure. The original
compiler freezes Campaign Style, page roles, display-copy contracts, layouts,
visual-performance contracts, selling-point proof and the search-cover blueprint.
Preserve the complete execution-rich `generation_request.prompt` unchanged.
Never replace it with a shortened custom JSON plan or a new prompt template.
Use the exact `imagegen_reference_paths` and declared `edit_target`.

## Workflow

1. `start --input <input.json> --session <session.json>`
2. `lock-identity --session <session.json> --identity <identity.json>`
3. `compile --session <session.json>` uses the unchanged original local compiler.
4. `authorize --session <session.json> --approval-text <submission authorization>`
   records task-submission authorization for generation and technical delivery.
5. Issue each original request with `generate-next --session <session.json> --out <request.json>`.
   The website may create six independent page-local snapshots from the same
   authorized session, setting only `active_page_index` to the real page index.
   No preceding image or synthetic audit/calibration result is required.
6. Submit one high-quality image call for each issued token, then bind its first
   returned original file using `record-generation --session <session.json> --token <token> --image <image>`.
   This selects the file as `GENERATED_NOT_REVIEWED` after technical checks.
7. After all six real files have been selected, `finalize --session <session.json> --output <manifest.json> --approval-text <submission authorization>`
   checks completeness and original-file hashes and records technical delivery.
   It does not claim a second human review or an AI visual verdict.

Independent page canvases can run concurrently under the website's bounded
rolling scheduler. Page-local sessions are merged using their real recorded
files before complete-set finalization. Do not populate fictional accepted
pages, set `style_calibrated` to true or manufacture passing judgments.

## Delivery and recovery

Request high-quality 1:1 images at least 1024×1024. Keep original image bytes;
do not upscale, pad, composite, overlay copy or re-encode a delivered result.
An issued token permits exactly one image call. Wait on the same call and use
`recover-generation` to bind the first completed file when necessary. Unknown
paid submission or download outcomes never authorize another paid call.
The website reuses durable originals and frozen prompts during explicit retry;
successful sibling images remain intact.

Every selected image and final manifest is `NOT_REVIEWED`. Program checks do
not establish visual PASS. The following commands return `BLOCKED` without
executing: audit-current, prepare-repair, calibrate-search, calibrate-style,
audit-set, apply-user-adjustment and restart-campaign. This version performs no
blocking AI audit, automatic aesthetic redraw or calibration-based replanning.
