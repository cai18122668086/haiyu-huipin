# Prompt and Mask Protocol

## Mask authority

Maintain two masks with different responsibilities:

- `selection.png`: the finalized exact authority for delivery. In interactive mode this is the user's original selection; in pre-authorized autonomous mode this is the audited effective selection.
- `mask_api.png`: model guidance; it may include limited padding for context.

In autonomous mode, also retain `user_selection.png` unchanged and record every pixel added to the effective selection.

Use a same-size PNG source and mask.

- Editable/API-mask pixel: alpha `0`.
- Locked/API-mask pixel: alpha `255`.
- Authority selection: white editable, black locked.

Never use the expanded API mask as the final compositing mask.

## Mask construction

1. Normalize the source before interpreting selection coordinates.
2. Threshold the original user selection into a binary same-size mask and preserve it unchanged.
3. In interactive mode, copy that mask unchanged to `selection.png`.
4. In pre-authorized autonomous mode, run the content-fit and safe-expansion preflight, save the audited result as `effective_selection.png`, and copy that exact mask to `selection.png`.
5. Expand a separate copy of the finalized authority by 0–32 pixels only when model context is necessary.
6. Invert the expanded copy into API alpha semantics.
7. For final feathering, blur the finalized authority and multiply it by the exact finalized authority. This makes feathering inward-only.

## GPT Image 2 request

For an OpenAI-compatible relay:

```text
POST {BASE_URL}/v1/images/edits
Authorization: Bearer {API_KEY}
Content-Type: multipart/form-data

model=gpt-image-2
image[]=@source.png
mask=@mask_api.png
prompt={MASTER_PROMPT}
n=1
size={VALID_WIDTH}x{VALID_HEIGHT}
quality=low|medium|high|auto
output_format=png
response_format=b64_json
```

Use `image` instead of `image[]` only when the relay requires it. Do not send `input_fidelity` for `gpt-image-2`. Never log or save the API key.

Valid dimensions have both edges divisible by 16, ratio at most 3:1, longest edge at most 3840 pixels, and 655,360–8,294,400 total pixels.

## Prompt contract

Insert the user's request verbatim into `{{USER_REQUEST}}`.

```text
任务类型：严格局部图片编辑 / masked image edit。
第一张输入图是唯一原始画布。透明蒙版区域用于告诉模型可编辑上下文，但最终允许修改的范围只以任务中最终确认的 authority selection 为准。

用户要求：
{{USER_REQUEST}}

规则：
1. 只完成用户要求的局部修改。
2. 延续原图的机位、透视、光向、曝光、白平衡、阴影、景深、锐度、颗粒、压缩质感和风格。
3. 不重新拍摄、重构、美化或顺手修改整张图。
4. 保持边缘遮挡、纹理、结构和真实光影连续。
5. 不添加说明、对比图、边框、标记、水印或额外文字。
6. 输出一张与原画布尺寸和构图一致的完成图。
```

Prompting and API masking are guidance, not pixel-lock guarantees.

## Generation ledger and visual checkpoint

Immediately after decoding a model response:

1. Save it once as an immutable raw artifact.
2. Record its generation ID, content hash, dimensions, prompt, and model-call count.
3. Inspect whether the requested edit is visually present before any resize, mask, or composite.
4. Record raw visual success separately from strict-lock success.

One model call equals one generation record. Reusing the same raw artifact for several local composites must not create several generation revisions. Store each local experiment as a post-processing attempt linked to the original generation ID.

If a raw result is visually successful:

- Keep it visible and selectable as a labeled visual candidate.
- Do not overwrite it with a locked composite.
- Do not describe later local mask failures as additional failed generations.
- Do not conclude that the task has no successful version solely because strict compositing fails.

## Final pixel lock

After model or local generation:

1. Decode and validate the generated image.
2. Resize it to source dimensions only if required.
3. Composite generated pixels with an inward-feathered authority mask.
4. Hard-restore every pixel outside the exact binary selection from the original.
5. Run `verify_selection_lock.py`.
6. Reject or repair the output when the outside changed-pixel count is nonzero.

Apply the hard lock after every operation that can leak: Gaussian blur, text antialiasing, shadow, dilation, template pasting, resizing, color correction, or alpha compositing.

## Dual-outcome handling

Track these outcomes independently:

- `raw_visual_success`: the model produced the requested appearance.
- `strict_lock_success`: the accepted final image has zero changed pixels outside the authority selection.

When the first is true and the second is false:

1. Preserve the raw candidate and all metrics.
2. Keep failed locked composites diagnostic-only and inactive.
3. Report `raw_visual_success_locked_failed` or `needs_reselection_for_strict_lock`.
4. Explain the exact blocker, such as a discontinuous face intersection or a requested silhouette clipped by the selection.
5. In interactive mode, ask for a revised selection only for strict-lock delivery. In autonomous mode, apply the declared safe-expansion or fail-safe policy. Do not imply that the model output itself failed.
6. If the user explicitly chooses visual-only delivery, label it non-pixel-locked and report the outside-selection changed-pixel count or ratio.

## Retry policy

Retry once for transport failures, HTTP 429, and HTTP 5xx. Do not automatically retry authentication, invalid-image, invalid-mask, moderation, or other user-correctable 4xx errors.

For a visual retry:

- Record one observable defect in the current best candidate and the single correction the retry is meant to produce.
- Do not retry for a speculative concern, a difference that is not visibly material to the user's request, or a desire to make an already successful result merely "more exact."
- Return to the original source.
- Use a smaller selection around the remaining defect.
- State one defect and one desired correction.
- Do not stack a new generation on an earlier generated result.
- Compare the retry with the current best candidate. If it is worse, discard it from active delivery and retain the previous best.

## Acceptance order

1. Inspect and record the immutable raw generation's visual success.
2. Verify the locked candidate's outside-selection pixel equality (`0` changed pixels).
3. Verify size, crop, composition, and perspective.
4. Verify the requested edit survived compositing.
5. Verify boundary texture and geometry.
6. Verify exact text and absence of unintended content.
7. Promote only a candidate that passes both visual and strict-lock gates.
