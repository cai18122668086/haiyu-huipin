# Fixed API First / image_gen Final Fallback

Use this before final image generation. The Xinghe deployment helper and fixed API route are the primary generation path; built-in `image_gen` is only the final fallback.

## Priority

1. Use the deployment helper from `XINGHE_IMAGE_GENERATOR`, then `%LOCALAPPDATA%\ApiCodexOneClick\tools\generate-image.ps1`.
2. If the deployment helper is missing, use bundled fixed-interface scripts: `scripts/generate_batch.py` for a full set, or `scripts/generate_image.py` for a single module.
3. If the deployment helper and bundled fixed-interface scripts are unavailable, fail, or cannot save/display the generated file, use built-in `image_gen` as the final fallback.
4. If no API key is configured and `image_gen` is also unavailable, return the complete prompt pack and `.env.example` guidance; do not invent images.

## Configuration

The installer or user environment should provide:

```dotenv
OPENAI_IMAGE_GENERATIONS_URL=https://xinghe.xin/v1/images/generations
OPENAI_IMAGE_EDITS_URL=https://xinghe.xin/v1/images/edits
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_API_KEY=your-api-key
```

Supported legacy aliases: `IMG_BASE_URL`, `IMG_MODEL`, `IMG_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_API_BASE`, `BASE_URL`, `IMAGE_MODEL`, `OPENAI_MODEL`, `OPENAI_API_KEY`, `API_KEY`.

Routing rule:

- reference images present -> use edits route / helper `-ReferenceImage` / script `--image`.
- text-only prompt -> use generations route / helper without `-ReferenceImage`.

## Deployment Helper Command

Use the exact user-requested size when provided. Examples:

- Horizontal default: `1536x864` or another 16:9 size supported by the helper.
- Ozon/Wildberries vertical: `900x1200` when the user asks for that size.

Use this for each module when a product/reference image exists:

```powershell
$helper = $env:XINGHE_IMAGE_GENERATOR
if (-not $helper) { $helper = Join-Path $env:LOCALAPPDATA 'ApiCodexOneClick\tools\generate-image.ps1' }
$promptFile = "<absolute prompt file path>"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper `
  -PromptFile $promptFile `
  -ReferenceImage "C:\path\to\product.png" `
  -OutputDir "<absolute output directory>" `
  -Size "<requested size, e.g. 900x1200 or 1536x864>" `
  -FileName "module-01.png"
```

For text-only generation, remove `-ReferenceImage`.

## Bundled Script Commands

Use only if the deployment helper is missing.

Batch:

```powershell
python .\scripts\generate_batch.py `
  --env-file .env `
  --prompt-dir prompts\product-detail-v1 `
  --image "C:\path\to\product.png" `
  --size "<requested ratio or pixel size, e.g. 900x1200 or 16:9>" `
  --resolution 2k `
  --output-dir generated-images\product-detail-v1
```

Single module:

```powershell
python .\scripts\generate_image.py `
  --env-file .env `
  --prompt-file prompts\module-01.txt `
  --image "C:\path\to\product.png" `
  --size "<requested ratio or pixel size, e.g. 900x1200 or 16:9>" `
  --resolution 2k `
  --output-dir generated-images\product-detail-v1
```

## Failure Handling

- If one module fails, retry once with a shorter prompt that preserves the locks.
- If API credentials are missing, stop script execution and output the prompt files plus `.env.example` instructions.
- If text renders poorly, use fewer labels and shorter copy.
- Do not create contact sheets, preview grids, stitched boards, or any locally assembled image output. They are disallowed and must never be treated as generated detail-page modules.
