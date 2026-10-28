---
name: ecommerce-model-pose-transfer
description: Pose-only transfer for ecommerce fashion-model images. Use when a user wants the person in one model image to imitate the complete pose from a second image while preserving the first image's person, body proportions, apparel, accessories, scene, lighting, and canvas. Requires exactly one model_source and one pose_reference. Do not use for identity transfer, model replacement, virtual try-on, garment replacement, background replacement, or style remixing.
---

# Ecommerce Model Pose Transfer

Edit `model_source`; use `pose_reference` only to show the target pose. The output must remain the person, outfit, and scene from `model_source`. Before editing, read and obey [the pose-transfer contract](references/pose-transfer-contract.md).

## Inputs

- Require exactly one clear single-person `model_source` and one unambiguous `pose_reference`.
- Bind roles explicitly. Never infer them from gender, clothing, filename, or image order.
- Resolve a catalog pose to its original-quality asset before generation; never send a thumbnail.
- If either role is ambiguous or unusable, stop and ask for a clearer image or crop.

## Execution

1. Use one multi-image edit request whenever the editor supports it.
2. Send `model_source` first as Image 1 and the only editable base.
3. Send `pose_reference` second as Image 2 and pose guidance only.
4. Use a short role-locked instruction:
   - Image 1 supplies the output identity, face, hair, natural body proportions, apparel, footwear, accessories, background, lighting, camera, canvas, crop, and overall appearance.
   - Image 2 supplies only body-relative pose geometry: torso lean, shoulder and pelvis lines, joint angles, limb overlap, feet relative to the pelvis and each other, stride relative to body height, toe directions, and weight-bearing foot.
   - Map that geometry onto Image 1's natural proportions and existing on-canvas person scale. Do not copy Image 2's body proportions or leg length.
   - Keep Image 2's viewer-left/viewer-right arrangement; never mirror, swap roles, or reconstruct Image 2's person, clothing, body traits, or scene.
   - Discard Image 1's old pose, support/contact relationships, and pose-conditioned garment geometry; rebuild them for the new pose.
5. Do not replace the visual pose reference with a long text or JSON description when the final editor can see both images. A geometry-only description is only a lower-fidelity fallback for a genuinely single-image editor.
6. Generate once. Do not run OpenPose, post-generation pose scoring, automatic acceptance, automatic correction, or automatic retry.

## Pose priorities

- Copy Image 2 as a connected, body-relative geometry, not as absolute canvas coordinates and not merely as an action category.
- Preserve torso lean, shoulder and hip lines, joint angles, asymmetry, limb crossing/overlap order, and viewer-left/viewer-right placement.
- For the lower body, preserve each foot relative to the pelvis and other foot, stride relative to body height, support/free-leg roles, knee bends, heel contact, and toe direction.
- Map the pose onto Image 1's natural limb lengths and current person scale. Never stretch limbs to reach Image 2's pixel locations or copy its proportions.
- Remove incompatible parts of Image 1's old pose, including seated/leaning support, furniture or prop contact, hand support, leg crossing, foot placement, and pose-dependent folds.

## Conflict priority

1. Valid inputs, safety, and anatomical validity.
2. Image 1 identity, natural body proportions, apparel identity, accessories, and scene identity.
3. Image 2 body-relative pose geometry.
4. Pose-driven garment drape, occlusion, contact shadows, and minimal source-consistent background reconstruction.
5. Beautification or style preference.
## Output

Return the one generated image for human review and disclose visible limitations. Do not claim pixel-exact identity, logo, text, or pose fidelity.
