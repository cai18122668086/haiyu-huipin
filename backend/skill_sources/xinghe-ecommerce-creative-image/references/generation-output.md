# Generation Output

## Direct Image Generation

当用户要做图时，直接调用部署工具 helper，不要只给 prompt。只有 helper 不存在或执行失败时，才使用内置 `image_gen` 作为最后兜底。

生成前只说一句：

```text
按【<输入类型>】处理：主打 <点击钩子>，默认生成 <数量> 张 <比例> 创意图。
```

## Image Prompt Structure

```text
Use the uploaded product image as a strict product reference.
Create one <ratio/size> CTR-first ecommerce ad creative for <product/category>.
Preserve the exact visible product appearance, package structure, colors, logo, material, scale, SKU, and key details.

CTR goal: users understand what is sold, what problem it solves, and why to click within 3 seconds.
Main headline text in Chinese: "<short click hook>"
Support text: "<short support line>"
Badges: "<badge 1>" / "<badge 2>" / "<badge 3>"

Composition: product large and clear, 45-70% of canvas, one dominant headline, up to three short badges, strong mobile readability, clear first-second-third visual hierarchy.
Visual style: premium ecommerce product photography, high-attention but trustworthy, clean lighting, strong contrast where useful, no clutter.
Avoid: fake logo, watermark, fake certification, fake reviews, fake sales data, unverifiable claims, dense tiny text, distorted product, copied competitor layout.
```

## Multi-Image Variation

用户要求多张时，每张换点击角度：

- 第 1 张：问题痛点型。
- 第 2 张：场景代入型。
- 第 3 张：对比选择型。
- 第 4 张：人群推荐型。
- 第 5 张：细节卖点型。

每张单独调用一次部署工具 helper；只有 helper 不存在或执行失败时，才每张单独调用一次内置 `image_gen` 兜底。不要一次混成拼图，除非用户明确要求拼图。

## Prompt-Only Exception

只有用户明确说“只要提示词、先不要出图、给我方案、不要调用生图”时，才输出 prompt。
