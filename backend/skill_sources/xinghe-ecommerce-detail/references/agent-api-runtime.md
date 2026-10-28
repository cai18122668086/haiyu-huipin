# External Agent/API Runtime

Use this reference only for building, testing, or integrating an external Agent and image API. For direct conversational image generation, follow the direct mode in `SKILL.md`.

## Validated architecture

```text
one product group (one or more angles)
  -> one structured vision-planning call
  -> product analysis + consistency anchors + page plan
  -> one independent gpt-image-2 edit request per page
  -> local JPEG files + manifest
```

For multiple products, use a pipeline:

```text
plan group N
  -> submit group N pages to the global image pool
  -> while they generate, plan group N+1
```

Use the bundled template at `assets/gpt-image-2-batch-runtime`. Copy it into the target project and configure it there.

Send every angle in a group to the planner in one multimodal message. Send the same ordered angles to each image edit as repeated `image` or `image[]` multipart fields. Tell the planner and image model that the images are complementary views of one product; flag visible variant conflicts `needs_confirmation`.

The planner is a mandatory production gate. Do not offer a static/template baseline workflow. If the planning request fails, cannot be parsed, fails schema validation, or returns the wrong number of screens, stop that group and surface the error. Never synthesize fixed fallback screens and continue into image generation.

## Default runtime contract

- Input: up to 10 product groups and 50 total images; each group may contain up to 10 angles of the same product.
- Compatibility input mode: one uploaded image remains one independent product group.
- Multi-angle input mode: group files explicitly; infer `group__angle.jpg` prefixes and known angle suffixes such as `group-front.jpg`, `group-正面.jpg`, `group-底部.jpg`, or `group-平铺.jpg`. Do not split arbitrary hyphenated product names unless the final suffix is a recognized angle.
- Output: 6–18 detail pages per group.
- Planner: configurable vision-capable chat model; validated default `gpt-5.6-terra`.
- Planner endpoint: OpenAI-compatible `/v1/chat/completions`.
- Planner calls: one structured call per group, not separate analysis and planning calls.
- Image model: `gpt-image-2`.
- Image endpoint: OpenAI-compatible `/v1/images/edits`.
- Requested size: `1024x1536`; record and report the actual returned dimensions because relays may normalize the image to another 3:4 size.
- Format: JPEG.
- JPEG quality: 92.
- Global image concurrency: 6.
- Timeout: 600 seconds per image request.
- Retry: retry failed requests only; one automatic retry is the tested baseline.
- Resume: skip successful pages whose files still exist and whose quality matches the current plan.
- Token accounting: store raw and normalized `usage` per planning call and per image request; mark missing usage as unreported.

Treat concurrency 6 as a tested default, not a provider guarantee. Reduce to 4 when the relay returns 429, times out, or shows worsening queue latency.

## Planner output

Return one JSON object containing:

- `analysis.category_key`
- `analysis.category_reason`
- `analysis.product_image_analysis`
- `analysis.product_consistency_anchors`
- `analysis.buyer_objections`
- `plan.campaign_style_lock`
- `plan.design_strength_lock`
- `plan.screens`

Each screen must contain:

- `role`
- `title`
- `label`
- `composition`
- `device`
- `difference`

Validate the exact requested screen count before starting image generation.

## Prompt-density contract

Compile these restrictions into every page prompt:

- One core selling point.
- One visual hero.
- One main title and one short label.
- At most one zoom window.
- At most two structural callouts.
- No multi-panel collage.
- No multiple crop windows.
- No repeated display of the same part.
- No invented functions, parameters, materials, certifications, efficacy, sales, or reviews.

These constraints improve clarity more reliably than raising image quality alone.

## All-high quality assignment

Assign `high` to every detail-page image request regardless of the dynamic
page role. The skill has no `medium` detail-image generation mode.

Store `high` in both `prompts.json` and `manifest.json`. The all-high policy
must have its own job-signature version so old medium outputs are never
silently reused as high outputs.

## Cost display

Before planning, show one truthful fixed estimate:

```text
estimated output cost = total pages * configured high unit price
```

After the dynamic plan is compiled, show the actual number of pending high
requests and their estimated output cost. The pending medium count must be
zero. Exclude already successful resume pages.

Do not assume relay pricing matches official reference pricing. Keep the high
unit price editable; a legacy medium price may remain only for reading older
records and must not affect new detail-image requests.

## Resume signature

Hash the source image bytes and all output-affecting settings:

For multi-angle input, hash ordered group boundaries, group labels, filenames, and every source image. Regrouping or changing any angle must invalidate the signature.

- base URL and image model
- planner model and mandatory-planning workflow version
- page count, category, language, and user product information
- image field name
- size, format, and JPEG quality
- quality-policy version
- prompt-density-policy version
- planning-policy version

Store one manifest per run. A page is reusable only when:

- status is `ok`
- expected file exists and is non-empty
- page number matches
- quality matches

Never automatically redraw a successful page for subjective QA.

## Performance metrics

Do not label request latency as production throughput.

Report:

- `average_api_latency`: average duration of individual image requests.
- `image_wall_time`: first image request start to last image save.
- `effective_seconds_per_page`: image wall time divided by completed page count.
- `job_wall_time`: job start to final manifest save.
- `planning_time`: time per planning call; note that later planning overlaps image generation.
- success/failure count and 429 count.
- planner input/output/total tokens and image-generation input/output/total tokens when reported.
- count of requests whose relay response omitted `usage`.

With concurrency, average API latency can be much larger than effective seconds per page.

### Validated benchmark example

One local relay test completed 5 groups / 30 images with concurrency 6:

- 30/30 succeeded.
- Average individual request latency: 75.1 seconds.
- Image wall time: about 403 seconds.
- Effective image throughput: about 13.4 seconds per page.
- Whole-job wall time: about 459 seconds.
- Average active concurrency: about 5.6.

This is evidence that the pipeline and concurrency logic work; it is not an SLA for another provider or load level.

## Secret handling

- Keep API keys in environment variables or an untracked local `.env`.
- Ship only `.env.example` with placeholder values.
- Never place a real key in `SKILL.md`, references, assets, manifests, logs, or source control.
- Mask keys in UI fields and error messages.

Recommended variables:

```text
TENGLI_API_KEY
TENGLI_BASE_URL
TENGLI_IMAGE_MODEL
TENGLI_VISION_MODEL
```

## Integration procedure

1. Copy `assets/gpt-image-2-batch-runtime` into the new Agent project.
2. Create a virtual environment and install `requirements.txt`.
3. Copy `.env.example` to `.env` and fill local values.
4. Run Python compilation checks.
5. Start with 1 product / 2 angles / 6 pages.
6. Verify planner JSON, quality routing, estimated cost, generated files, and manifest.
7. Force one invalid planner response and confirm that no image request is submitted for that group.
8. Test resume by rerunning unchanged inputs and confirming zero successful-page requests.
9. Test one controlled image failure and confirm only that page retries.
10. Test concurrency 6; fall back to 4 on 429 or queue degradation.
11. Run a no-cost stress test at 10 groups / 5 angles / 8 pages (50 inputs, 80 tasks).
12. Only then run the full paid workload, after confirming relay multipart limits and expected cost.

## Acceptance checklist

- Planner returns the requested page count in one call.
- The runtime has no static/template baseline branch.
- Invalid planning prevents all image requests for that group.
- Every prompt starts with the 3:4 contract.
- Every page has one title, one label, and one core selling point.
- Every detail-page image request routes to high.
- The pending medium count is zero.
- Requests use JPEG 92 and `size=1024x1536`; actual returned dimensions are inspected or reported without local resampling.
- Successful pages survive resume without another request.
- Failed pages can be retried independently.
- Cost appears before generation.
- Multiple angles in one group reach both the planner and every image-edit request.
- Regrouping or changing one angle invalidates resume safely.
- Per-request and aggregate token usage is stored when reported; missing usage is explicit.
- Metrics separate request latency from wall time and throughput.
- No real key exists in the distributable skill or project template.
