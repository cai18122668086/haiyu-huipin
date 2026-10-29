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

1. First attempt the deployment helper written by the installer for the actual image generation call.
2. Resolve the helper from `XINGHE_IMAGE_GENERATOR` first, then `%LOCALAPPDATA%\ApiCodexOneClick\tools\generate-image.ps1`.
3. The deployment helper already knows the fixed endpoints, model, API key, output saving, and Markdown preview output. Do not ask the user for a key.
4. The helper routes reference-image requests to `https://xinghe.xin/v1/images/edits` and text-only requests to `https://xinghe.xin/v1/images/generations`.
5. Use the fixed model: `gpt-image-2`.
6. If the deployment helper is missing, unavailable, fails, or cannot save/display the generated image, use the built-in Codex `image_gen` tool as the final fallback.
7. Do not call built-in `image_gen` before resolving and attempting the deployment helper.
8. All detail-page images default to a consistent vertical `3:4` aspect ratio; every prompt must include `vertical 3:4 e-commerce detail page image`.
9. Every detail-page image must have a main title and at least one selling-point label or short phrase. Do not generate pure visuals without selling-point copy.
10. Final images must be generated directly by the deployment helper or by built-in `image_gen` as the final fallback. Do not use local scripts to stitch images, overlay text, make collages, replace backgrounds, composite final images, manually call direct HTTP / SDK-default image APIs, or create placeholder/local-composited images.

## Deployment Helper Command

Use this section before built-in `image_gen`. The deployment helper is the primary generation path.

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

For reference-image generation, join every product/reference image path with semicolons and pass the joined string to `-ReferenceImage`. Do not pass a PowerShell array or comma-separated multiple paths directly to `-ReferenceImage`, because later arguments may be mis-bound as `TimeoutSec` or other parameters.

```powershell
$promptFile = "<absolute prompt file path>"
$referenceImages = @("C:\path\to\product-front.png", "C:\path\to\product-side.png")
$referenceImageArg = ($referenceImages -join ';')
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper `
  -PromptFile $promptFile `
  -ReferenceImage $referenceImageArg `
  -OutputDir "<absolute output directory>" `
  -Size "1024x1536" `
  -FileName "detail-01.png"
```

The helper prints the absolute saved image path and a Markdown image tag. Use the printed saved path for final delivery and preview.

## Staggered Concurrent Helper Calls

When more than one final image must be generated, submit helper calls with staggered concurrency:

- Submit the first helper call as a background job.
- Wait 3 seconds.
- Submit the second helper call without waiting for the first image to finish or land on disk.
- Repeat until all planned images are submitted.
- After all jobs are submitted, wait for all jobs, collect their helper output, and verify saved files.

Use one independent prompt file and one independent output filename per image. If reference images are used, pass a single semicolon-joined `ReferenceImageArg` string. Do not share one output filename across jobs.

```powershell
$serialFallback = $false
$jobs = @()
try {
  foreach ($task in $imageTasks) {
    $jobs += Start-Job -ScriptBlock {
      param($helper, $promptFile, $referenceImageArg, $outputDir, $size, $fileName)
      if ($referenceImageArg) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -PromptFile $promptFile -ReferenceImage $referenceImageArg -OutputDir $outputDir -Size $size -FileName $fileName
      } else {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -PromptFile $promptFile -OutputDir $outputDir -Size $size -FileName $fileName
      }
    } -ArgumentList $helper, $task.PromptFile, $task.ReferenceImageArg, $task.OutputDir, $task.Size, $task.FileName
    Start-Sleep -Seconds 3
  }
  $jobs | Wait-Job | Out-Null
  $results = $jobs | Receive-Job -ErrorAction Stop
} catch {
  $serialFallback = $true
} finally {
  if ($jobs.Count -gt 0) {
    if ($serialFallback) { $jobs | Stop-Job -ErrorAction SilentlyContinue }
    $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
  }
}
if ($serialFallback) {
  $results = @()
  foreach ($task in $imageTasks) {
    $outPath = Join-Path $task.OutputDir $task.FileName
    if (Test-Path -LiteralPath $outPath) { continue }
    if ($task.ReferenceImageArg) {
      $results += & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -PromptFile $task.PromptFile -ReferenceImage $task.ReferenceImageArg -OutputDir $task.OutputDir -Size $task.Size -FileName $task.FileName
    } else {
      $results += & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -PromptFile $task.PromptFile -OutputDir $task.OutputDir -Size $task.Size -FileName $task.FileName
    }
  }
}
```

For a single image, keep the normal synchronous helper call. If staggered concurrency fails, times out, or causes rate limits, retry failed images one by one with the same helper arguments.

## Request Shape

Do not manually call direct HTTP or SDK-default image APIs as an automatic fallback. The deployment helper owns the endpoint, model, key selection, request shape, output saving, and Markdown preview behavior. If the helper cannot complete generation, use built-in `image_gen` as the final fallback.

For `size`, prefer a value that expresses vertical `3:4`. If the endpoint does not support an exact ratio, strongly constrain the prompt with `vertical 3:4 e-commerce detail page image, consistent portrait aspect ratio across the full set`.

When product images are provided, prefer using the original image as a product-identity reference input through the deployment helper. The reference image locks product identity only; it must not lock the source photo background, camera angle, crop, placement, lighting setup, or plain white-background product-photo composition. If reference images are unsupported by the current runtime, write the same `Product Consistency Anchors` / `Product Identity Lock` into every prompt and explicitly require consistency in visible product appearance, pattern/logo/nameplate placement, shape, proportions, structural parts, and relative position.

If the reference input is a white-background single-product photo, every prompt must also include: `Do not replicate the white-background reference as a white-background single-product image; create a designed ecommerce detail-page image with a new composition, buyer-facing headline, selling labels, and visual proof.`

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
- Check dimensions, file existence, and image count.

Not allowed:

- Create contact sheets, stitched previews, image grids, comparison boards, or any locally assembled image output.
- Use local scripts to overlay final-image copy.
- Use local scripts to assemble product, background, and labels into a final image.
- Use local scripts to stitch multiple generated images into one deliverable or preview image.
- Crop and recombine multiple generated images into a final detail page.
- Treat an image with post-edited text fixes as a qualified final image.

Collage-style layouts are allowed only when the full collage-style detail module is produced directly by the image-generation tool/API in one generation request. They must not be assembled from local image pieces.

If there are text errors, weak design, or product drift, do not automatically rewrite prompts or regenerate the affected image. Record the issue only as an optional review item unless the user explicitly asks for regeneration.

## Final Delivery Set Rule

- Images that are successfully generated and saved are the delivered images for this run.
- The final response lists the delivered image paths and shows Markdown image previews.
- Do not use final qualified version, problem image, or rejected image as an automatic filtering mechanism.
- If the user later asks to regenerate a specific image, generate the new version within the user-specified scope and update the delivery list.

## Failure Handling

Deliver a prompt pack without images only when both generation paths have been attempted or resolved and neither can produce saved image files:

- The deployment helper is missing, unavailable, fails, cannot read its credentials, cannot reach the network, or cannot save/display the result; and
- built-in `image_gen` is also unavailable, fails, cannot reach the network, or cannot save/display the result.

When failure occurs, explain both sides of the failure where available, such as missing helper path, helper credential issue, helper HTTP status, helper endpoint error summary, unavailable built-in image tool, built-in image tool error, or unreachable network. Do not describe prompt-only output as a completed image-generation task.
