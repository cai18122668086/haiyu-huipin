---
name: xinghe-wanneng-shengtu-3-0
description: Directly generate high-fidelity all-category ecommerce product images from uploaded product photos. Use when users ask for 商品主图, 电商主图, 淘宝/天猫/Amazon/TikTok Shop product images, 场景图, 白底图, 模特图, 换模特, 模特换装, 试穿, 换背景, 换场景, 换颜色, 详情图, 广告图, or product-image SOPs. Default behavior is to call image generation directly, not to stop at prompts, unless the user explicitly requests prompts/SOP only.
---

# 星河万能生图3.0

## Core Mandate

Default to **direct image generation**. When the user uploads product photo(s) and asks for ecommerce images, call the available image generation/editing tool immediately with a complete production prompt. Do **not** ask “是否出图” and do **not** only output prompts unless the user explicitly asks for prompts, SOP, templates, or planning.

Treat uploaded product photos as the only visual truth. Product fidelity outranks beauty, drama, marketing, and creative styling.

## Default Behavior

When the user gives product photo(s) without detailed requirements:

1. Generate **one** `1:1` Taobao/Tmall ecommerce main image.
2. Use a clean premium commercial scene.
3. Keep the product centered, dominant, and visually consistent with the reference.
4. Add no extra promotional text, price, watermark, UI, fake certification, or fake badge.
5. After generation, briefly report task type, aspect ratio, fidelity focus, style, and caveat.

## Minimal Workflow

1. Identify the user task and product category from the images/request.
2. If task is unclear, choose default `scene_main`.
3. Build the image prompt with:
   - Product Consistency Lock.
   - Task-specific edit scope.
   - Platform/category default.
   - Quality Lock.
   - Negative constraints.
4. Call image generation once per requested output.
5. If output may be too creative or fidelity is weak, offer a conservative retry direction; do not blame the user.

## Progressive Loading

Read only the reference file needed for the current task:

- Multi-image inputs or competitor/reference ambiguity: `references/input-roles.md`.
- Platform-specific defaults: `references/platform-defaults.md`.
- Task recipes for scene, white background, model, try-on, background, color: `references/task-recipes.md`.
- Food, health, beauty, mother/baby, claims, certifications: `references/compliance.md`.
- Output inspection, failure diagnosis, and retry strategy: `references/qa-retry.md`.

## Product Consistency Lock

Include this in every generation prompt:

```text
Use all uploaded product reference images as strict visual source of truth. Keep the product visually consistent with the reference as much as the model allows: same silhouette, structure, dimensions, color, material, texture, finish, packaging, logo, printed text, pattern, accessories, edge shape, and scale relationship. Do not invent new logos, labels, certifications, claims, accessories, buttons, decorations, stitches, straps, caps, handles, seams, textures, or package copy. If a detail is unclear, preserve the visible design instead of hallucinating a new one. Prioritize product fidelity over beauty, drama, or creative styling.
```

## Quality Lock

Use quality language without promising physical output resolution:

```text
Premium commercial product photography, 8K high-definition look, crisp focus, fine texture detail, realistic shadows, accurate perspective, clean edges, no blur, no watermark, no compression artifacts, no distorted product geometry.
```

## Direct Generation Rule

- If the user asks “做图 / 生成 / 换 / 改 / 出主图 / 白底图 / 场景图 / 模特图”, generate directly.
- If user gives only a product image and no text, generate one default main image directly.
- Ask a question only when a missing value blocks the task, e.g. change_color without target color, or platform/legal constraint that cannot be safely inferred.
- For prompts-only requests, do not call image generation; provide structured prompts.

## Final Response

After generation, keep the response short:

- Task type.
- Aspect ratio.
- Fidelity focus.
- Style.
- Any compliance/fidelity caveat.

## Generation Tool Fallback

默认优先使用部署工具写入的 helper。只有在 helper 不存在、执行失败，或无法把结果稳定保存为本地文件并在对话中预览时，才把内置 `image_gen` 作为最后兜底。不要使用 SDK 默认 key 或本地绘图脚本作为替代。执行生图任务时应先解析并调用 helper，不要在 helper 尝试前改用 `image_gen`。

helper 解析顺序：

```text
$env:XINGHE_IMAGE_GENERATOR
%LOCALAPPDATA%\ApiCodexOneClick\tools\generate-image.ps1
```

接口规则：

- 有产品图、参考图、竞品图等参考图片时，调用 helper 并通过 `-ReferenceImage` 传入所有参考图片；helper 会路由到 `https://xinghe.xin/v1/images/edits`。
- 没有参考图片、纯文本起图时，不传 `-ReferenceImage`；helper 会路由到 `https://xinghe.xin/v1/images/generations`。
- 模型固定使用部署工具配置的 `gpt-image-2`。
- API Key 使用部署工具安装时校验并写入的 key，不向用户询问，不在回复中输出、保存或复述真实 key。
- 完整生图提示词必须先写入 UTF-8 prompt 文件，再用 `-PromptFile` 调用；不要把长提示词直接拼到命令行。
- 每张图单独调用一次 helper，保存到本地输出目录。
- helper 会输出本地绝对路径和 Markdown 图片预览；最终回复必须使用本地绝对路径展示图片。

PowerShell 示例：

```powershell
$helper = $env:XINGHE_IMAGE_GENERATOR
if (-not $helper) { $helper = Join-Path $env:LOCALAPPDATA 'ApiCodexOneClick\tools\generate-image.ps1' }
$promptFile = "<absolute prompt file path>"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper `
  -PromptFile $promptFile `
  -ReferenceImage "C:\path\to\product.png", "C:\path\to\reference.png" `
  -OutputDir "<absolute output directory>" `
  -Size "1024x1024" `
  -FileName "image-01.png"
```

无参考图时删除 `-ReferenceImage ...` 参数。

如果 helper 不存在或执行失败，不要改用 Pillow、matplotlib、SVG、canvas 或占位图。只有当前会话明确暴露可用的内置 `image_gen` 时，才把它作为最后兜底；否则报告 helper 缺失或执行失败。

