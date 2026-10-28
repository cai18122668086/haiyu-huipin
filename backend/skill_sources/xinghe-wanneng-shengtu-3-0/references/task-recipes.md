# Task Recipes

Use the matching recipe to build the final image generation prompt. Always include Product Consistency Lock and Quality Lock from `SKILL.md`.

## Task Selection

- No detailed request: `scene_main`.
- “白底图 / white background”: `white_background`.
- “场景图 / 主图 / 氛围图”: `scene_main` or `change_scene`.
- “换背景”: `change_background`.
- “换场景”: `change_scene`.
- “生成模特图 / 上身图 / 试穿”: `model_image`.
- “更换模特 / 换模特”: `change_model`.
- “模特换装 / 穿到模特身上”: `try_on`.
- “换颜色 / 改颜色”: `change_color`; ask for target color if missing.

## scene_main

```text
Task: ecommerce 1:1 main product scene image.
Allowed edit scope: create only scene, lighting, surface, and supporting props; product must stay from reference.
Scene: clean premium commercial studio or realistic category-relevant scene.
Composition: square 1:1, product centered or slightly off-center, product occupies 60–82% of canvas, clean crop-safe margin.
Text policy: no extra promotional text, no price, no watermark.
Category adaptation: apparel/shoes/bags may use a natural model; packaged goods, food, home, digital, and beauty should stay product-first.
```

## white_background

```text
Task: white-background product image.
Allowed edit scope: only remove/replace background with pure white.
Background: #FFFFFF, subtle natural contact shadow, clean product edges.
Composition: square 1:1, product centered, complete outline visible, 8–12% safety margin.
Risk lock: do not redraw product, add props, alter package text, alter logo, alter structure, or change color.
```

## model_image

```text
Task: model display image.
Allowed edit scope: generate model and scene only; product style must match reference.
Model: natural realistic ecommerce model, confident posture, true body proportions.
Composition: product clearly visible, key details unobstructed, full body when showing apparel or shoes.
Risk lock: do not change garment cut, length, lapel, sleeve, button count, pocket placement, shoe shape, bag structure, hardware, print, color, or fabric.
```

## change_model

```text
Task: replace model.
Allowed edit scope: change only model identity, pose, age impression, or styling mood requested by user.
Keep: product, garment fit, drape, silhouette, button placement, color, fabric, and visible details.
Risk lock: model can change; product cannot.
```

## try_on

```text
Task: model try-on / outfit transfer.
Allowed edit scope: place the reference product naturally on the model.
Keep: product cut, structure, color, pattern, material, length, accessories, and scale.
Risk lock: do not improve or redesign the SKU; preserve the original SKU.
```

## change_scene

```text
Task: scene swap.
Allowed edit scope: change only environment, lighting, surface, and supporting props.
Scene: match product category and buyer use case.
Risk lock: scene changes; product shape, color, logo, packaging, label layout, and proportions do not.
```

## change_background

```text
Task: background replacement.
Allowed edit scope: replace background only.
Background: user-specified background or clean premium studio background.
Composition: keep product centered and complete.
Risk lock: do not redraw product; preserve edges, text, logo, color, and scale.
```

## change_color

```text
Task: color variant.
Allowed edit scope: change only the user-specified color area to the target color.
Keep: material texture, highlight behavior, logo, printed text, hardware, stitching, structure, packaging, and all non-target colors.
Risk lock: if no target color is specified, ask for the target color before generating.
```

## Negative Constraints

```text
No watermark, no fake logo, no changed product structure, no changed product ratio, no changed package text, no fake certification, no sales data, no medical efficacy wording, no absolute guarantee, no dense small text, no hard-sell button copy, no background object blocking the product, no low-resolution artifacts.
```
