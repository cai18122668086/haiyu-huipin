# Input Roles

Use this file when the user uploads multiple images or mixes product photos with model, scene, competitor, or style references.

## Role Priority

1. **Primary product reference**: the SKU to preserve. Its shape, color, structure, logo, package, text layout, material, and scale are highest priority.
2. **Detail reference**: close-ups showing texture, buttons, seams, labels, accessories, print, or package text. Use to improve fidelity, not as separate products.
3. **Packaging/SKU reference**: boxes, cans, sachets, bottles, hangtags, color variants. Preserve visible package layout and variant relationships.
4. **Model reference**: use only for body/pose/identity/style if the user asks for model output. Never copy clothing from model reference unless it is the product.
5. **Scene/style reference**: borrow lighting, composition, surface, mood, or background only. Do not copy unrelated products or logos.
6. **Competitor reference**: analyze layout/click mechanism only. Do not copy brand, exact text, icons, claims, or protected design elements.

## Ambiguity Handling

- If several uploaded images show similar products, choose the image most likely intended as the product and preserve all visible common features.
- If images conflict, preserve the most complete/front-facing product image and use detail images only for visible details.
- If a user explicitly names one image as target, obey that image as the target.
- If target product cannot be identified, ask one concise clarification before generating.

## Prompt Language

Add one compact line:

```text
Input image roles: Image 1 is the primary product reference; Image 2 is detail/packaging/model/scene reference. Preserve product from the primary product reference; use other images only for their stated role.
```
