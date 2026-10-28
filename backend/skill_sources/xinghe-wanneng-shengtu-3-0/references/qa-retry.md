# QA and Retry

Use this file after generation or when deciding whether to retry.

## Output QA Checklist

Check quickly:

- Product same SKU: shape, structure, color, material, logo, label, text layout, accessories.
- Scale and perspective natural.
- No missing or extra product parts.
- Background/props do not block product.
- No fake certification, fake badge, fake claim, fake review, fake data.
- No watermark, UI, price, dense text, or low-resolution artifact.
- For apparel: cut, collar, sleeves, buttons, pockets, trouser/skirt shape, fabric, and fit remain consistent.
- For packaging: logo, color blocks, package layout, can/bottle/box/sachet shape remain consistent.

## Retry Strategy

If product fidelity fails:

1. Reduce creativity.
2. Use white/gray/off-white studio background.
3. Keep original camera angle closer to reference.
4. Remove props and model if they caused drift.
5. Strengthen Product Consistency Lock and list exact visible details.

If text/label is corrupted:

1. Avoid adding new text.
2. Ask model to preserve packaging text impression rather than recreate tiny text.
3. Use product farther from extreme close-up if text fidelity is unstable.

If model task changes garment:

1. Use ghost mannequin, face-cropped model, or simple studio model pose.
2. Add exact garment lock: button count, collar/lapel, sleeve, waist, hem, pants/skirt shape.
3. Avoid dynamic poses that stretch or hide structure.

If scene overwhelms product:

1. Remove background props.
2. Increase product size to 70–85% of canvas.
3. Use shallow depth of field and clean studio surface.

## Final Caveat Language

Use short caveats:

- “已按强保真约束生成，但生成式模型可能存在微小包装文字/细节偏差。”
- “如需更稳上架，建议再做一版白底/灰底保守图。”
