# Model Pose Transfer Contract

Use this contract to change only the pose of the person in `model_source` by visually following `pose_reference`.

## 1. Fixed image roles

- **Image 1 ? `model_source`: editable base and appearance authority.** It alone supplies the output person's identity, face, hair, skin tone, apparent age, natural body proportions, clothing, footwear, accessories, visible graphics/text, background, lighting, camera, canvas, crop, and overall image identity.
- **Image 2 ? `pose_reference`: pose authority only.** It supplies head and body orientation, joint arrangement, balance, limb placement, hand and foot placement, weight distribution, line of action, and pose rhythm.
- The result MUST be Image 1's person in Image 1's outfit and scene adopting Image 2's pose.
- The result MUST NOT be Image 2's person wearing Image 1's outfit or imitating Image 1's old pose.
- Require exactly one usable image for each role. Never swap, merge, or infer roles from visual attributes.
- Catalog references MUST resolve to the original-quality pose asset; thumbnails are UI-only.

## 2. Direct multi-image execution

When the editor accepts multiple images:

1. Make one edit request containing both original-quality images.
2. Place `model_source` first and `pose_reference` second.
3. State that Image 1 is the only editable base and only appearance source.
4. State that Image 2 is visual pose guidance only and contributes no identity, body traits, apparel, footwear, accessories, props, background, lighting, or style.
5. Tell the editor to discard Image 1's old pose and adopt Image 2 as body-relative pose geometry, not as absolute canvas coordinates or a generic action example.
6. Map the reference geometry onto Image 1's natural proportions and existing person scale; keep viewer-left/viewer-right and never copy Image 2's body proportions or leg length.
7. Do not attach the full Skill documents or replace the visible reference with a long JSON skeleton. The model should directly compare the two images.
8. A text/geometry-only fallback is allowed only when the final editor genuinely cannot receive both images; disclose that pose fidelity may be lower.

The role labels are semantic constraints, not a license to mix both people. Image order and the explicit anti-role-reversal instruction are both required.

## 3. Authorized change

The edit may change only what the new pose forces:

- head angle, gaze direction, neck, shoulder line, torso orientation and lean;
- pelvis line and shift, center of gravity, arm and hand placement;
- hip, knee, ankle, heel, and toe placement;
- support/free-leg roles, stride, crossing/depth order, and ground contact;
- pose-dependent garment drape, folds, compression, stretch, occlusion, and shadows;
- minimal nearby source-consistent background reconstruction exposed by moved limbs.

Image 1's old joint arrangement, leg crossing, foot placement, seated/leaning contact, prop/body contact, and pose-induced folds are not protected facts.

## 4. Prohibited transfer from Image 2

Do not copy or reconstruct Image 2's:

- person, face, hair, skin, age, body shape, limb lengths, or gender presentation;
- clothing, footwear, accessories, jewelry, props, logos, prints, colors, or materials;
- background, objects, lighting, camera treatment, color grading, or style;
- framing or crop when it conflicts with the source canvas.

Do not add a second person, hybridize faces or bodies, or convert the task into face swap, virtual try-on, garment transfer, or scene remixing.

## 5. Complete pose matching

Treat Image 2 as one connected, body-relative pose geometry:

1. Match head direction, torso lean/rotation, shoulder line, pelvis line, and center of gravity.
2. Match each arm's shoulder-elbow-wrist-hand chain, palm direction, and body contact when visible.
3. Match each leg's hip-knee-ankle-foot chain, foreground/background order, crossing, and negative space.
4. Keep every visible part on the same viewer-left or viewer-right side as Image 2; do not mirror or exchange limbs.
5. Express reach and stride relative to the reference body, then map them onto Image 1's natural proportions and existing on-canvas scale.
6. Rebuild Image 1's clothing over the new articulation instead of preserving its old silhouette or folds.

Do not copy Image 2's absolute pixel coordinates, body proportions, or leg length. Do not simplify a dynamic or asymmetric reference into a generic pose category.

## 6. Lower-body and foot rules

For every visible standing or walking pose:

- Preserve the support/free-leg roles, pelvis weight shift, and each knee's bend and depth.
- Preserve each foot's position relative to the pelvis and the other foot, plus stride width/depth and crossing order.
- Preserve flat, heel-raised, forefoot, toe-touch, airborne, or partly occluded contact and the toe direction.
- Keep each leg connected to the corresponding viewer-side hip; do not mirror or swap complete leg chains.
- Keep Image 1's natural femur and lower-leg proportions and footwear. If the reference reach does not fit, adapt the whole-person placement or stride rather than lengthening a leg.
- Remove Image 1's incompatible seated, leaning, furniture, prop, hand-support, or foot-support relationships before rebuilding contact shadows.
## 7. Preservation rules

Preserve from Image 1:

- recognizable identity, face, hair, skin tone, apparent age, and natural proportions;
- garment identity, cut, material, colors, layering, hem lengths, closures, prints, logos, and accessories;
- source background, lighting direction, shadows, camera style, aspect ratio, and canvas;
- original crop semantics unless modest whole-person repositioning is required to fit the target pose.

Preserve garment identity, not old pose-conditioned fabric geometry. Pose-driven redraping is required.

## 8. Human review only

Return the single generated result for human review. Check:

- no role reversal or Image 2 appearance leakage;
- Image 1 identity, outfit, scene, and natural proportions remain recognizable;
- Image 1's old pose is not visibly retained;
- head, torso, arms, hands, pelvis, legs, knees, ankles, heels, and toes follow Image 2 coherently;
- support leg, stride, crossing order, foot contact, and toe direction are correct;
- no stretched legs, broken joints, duplicated limbs, extra people, or detached feet;
- canvas, crop, and background remain source-consistent.

Do not run post-generation pose detection, scoring, automatic acceptance, automatic correction, or automatic retry. Do not claim exact compliance when visual deviations remain.
