# Generation Tools

Read this file for direct image generation. Unless the user explicitly asks for prompts only, do not stop at a prompt pack.

## Audit / Regeneration Override

- Disable automatic audit-and-regenerate behavior. Once generation succeeds, files are saved to local paths, and necessary file-existence checks pass, proceed to delivery.
- Do not automatically rewrite prompts or regenerate images because of text errors, weak design, product drift, imperfect dimensions, or repeated page structures.
- If potential issues are noticed, list them only in the final response as `Optional Review Items / Items Needing Confirmation`. Call the generation tool again only when the user explicitly asks to regenerate, redo, re-create, or fix a specific image.
- This section overrides any older rule below that implies post-QA regeneration, final qualified versions, keeping only qualified outputs, or excluding problem images automatically.

## Save Output Rule

- After every successful generation, save the image to a local file path.
- The final response must list the local path for each image; do not only say that generation is complete.
- In chat surfaces that support image display, show generated images with Markdown image syntax: `![description](absolute local path)`.
- If the local file cannot be confirmed, do not claim image generation is complete. Explain the failure reason and keep the executable prompt available.

## Priority

1. First attempt the built-in Codex `image_gen` tool for the actual image generation call.
2. Do not inspect, test, resolve, or mention the deployment helper before attempting `image_gen`; no `Test-Path`, no `Get-Command`, no `$env:XINGHE_IMAGE_GENERATOR` lookup, and no PowerShell helper command may run before the `image_gen` attempt.
3. If `image_gen` is unavailable, fails, or cannot save the generated image to a local path, use the deployment helper written by the installer. Resolve it from `XINGHE_IMAGE_GENERATOR` first, then `%LOCALAPPDATA%\ApiCodexOneClick\tools\generate-image.ps1`.
4. The deployment helper already knows the fixed endpoints, model, API key, output saving, and Markdown preview output. Do not ask the user for a key.
5. The helper routes reference-image requests to `https://xinghe.xin/v1/images/edits` and text-only requests to `https://xinghe.xin/v1/images/generations`.
6. If the deployment helper is missing or fails, report the helper failure instead of creating placeholder/local-composited images.
7. Use the fixed model: `gpt-image-2`.
8. All detail-page images default to a consistent vertical `3:4` aspect ratio; every prompt must include `vertical 3:4 e-commerce detail page image`.
9. Every detail-page image must have a main title and at least one selling-point label or short phrase. Do not generate pure visuals without selling-point copy.
10. Final images must be generated directly by `image_gen` or the deployment helper. Do not use local scripts to overlay text, make collages, replace backgrounds, composite final images, or silently switch to SDK/default direct API calls.

## Deployment Helper Command

Use this section only after `image_gen` has been attempted and has failed, is unavailable, or cannot save/display the result. Do not use it for preflight checks.

Resolve the helper path at runtime:

```powershell
$helper = $env:XINGHE_IMAGE_GENERATOR
if (-not $helper) { $helper = Join-Path $env:LOCALAPPDATA 'ApiCodexOneClick\tools\generate-image.ps1' }
```

For text-only image generation:

```powershell
$promptFile = "<absolute prompt file path>"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper `
  -PromptFile $promptFile `
  -OutputDir "<absolute output directory>" `
  -Size "1024x1536" `
  -FileName "detail-01.png"
```

For reference-image generation, pass every product/reference image with `-ReferenceImage`:

```powershell
$promptFile = "<absolute prompt file path>"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper `
  -PromptFile $promptFile `
  -ReferenceImage "C:\path\to\product-front.png", "C:\path\to\product-side.png" `
  -OutputDir "<absolute output directory>" `
  -Size "1024x1536" `
  -FileName "detail-01.png"
```

The helper prints the absolute saved image path and a Markdown image tag. Use the printed saved path for final delivery and preview.

## Request Shape

Do not manually call direct HTTP or SDK-default image APIs as an automatic fallback. The deployment helper owns the endpoint, model, key selection, request shape, output saving, and Markdown preview behavior.

For `size`, prefer a value that expresses vertical `3:4`. If the endpoint does not support an exact ratio, strongly constrain the prompt with `vertical 3:4 e-commerce detail page image, consistent portrait aspect ratio across the full set`.

When product images are provided, prefer using the original image as a reference input through the deployment helper. If reference images are unsupported by the current runtime, write the same `Product Consistency Anchors` / `Product Consistency Lock` into every prompt and explicitly require consistency in visible product appearance, pattern/logo/nameplate placement, shape, proportions, structural parts, and relative position.

Before direct image generation, read `design-strength-system.md` and add the `Design Strength Lock` to every prompt. Do not rely only on generic adjectives such as clean, premium, or modern.

## Multi-Image Rule

- One detail-page image corresponds to one independent prompt.
- One detail-page image corresponds to one independent generation request.
- Do not generate multi-screen collages in one request.
- Do not place multiple detail pages on the same canvas.

## Response Handling

The endpoint or helper may return any of the following:

- Absolute local path: confirm the file exists and use it in final delivery.
- Markdown image tag: include it in the final response if the local file exists.
- `url`: download the image and save it to a local output directory.
- `b64_json`: decode it into an image file and save it.
- Task ID: wait for completion according to the polling method returned by the endpoint.

The final response must list generated file paths. If saved files cannot be confirmed, do not claim that image generation is complete.

When displaying images, use absolute paths, for example: `![01](C:\path\to\01.png)`.

## Allowed Post-Processing

Allowed:

- Download, copy, or rename generated files.
- Create a contact sheet for manual preview only; the contact sheet must not be delivered as a final detail page.
- Check dimensions, file existence, and image count.

Not allowed:

- Use local scripts to overlay final-image copy.
- Use local scripts to assemble product, background, and labels into a final image.
- Crop and recombine multiple generated images into a final detail page.
- Treat an image with post-edited text fixes as a qualified final image.

If there are text errors, weak design, or product drift, do not automatically rewrite prompts or regenerate the affected image. Record the issue only as an optional review item unless the user explicitly asks for regeneration.

## Final Delivery Set Rule

- Images that are successfully generated and saved are the delivered images for this run.
- The final response lists the delivered image paths and shows Markdown image previews.
- Do not use final qualified version, problem image, or rejected image as an automatic filtering mechanism.
- If the user later asks to regenerate a specific image, generate the new version within the user-specified scope and update the delivery list.

## Failure Handling

Deliver a prompt pack without images only in these cases:

- The configured API key cannot be read.
- The current environment cannot make HTTP requests.
- The deployment helper is missing or returns an unrecoverable failure.

When failure occurs, explain the specific reason, such as missing helper path, HTTP status, endpoint error summary, missing credentials, or unreachable network. Do not describe prompt-only output as a completed image-generation task.
