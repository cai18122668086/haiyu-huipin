# Quality Control

This file is only an optional manual review checklist. It is not required after direct image generation, and review is not a required delivery step.

## Audit / Regeneration Override

- Disable automatic audit-and-regenerate behavior. This file must not trigger automatic regeneration, automatic rejection, or automatic replacement of final images.
- Checklist items are only for manual review or for `Optional Review Items / Items Needing Confirmation` in the final response.
- Call generation tools again only when the user explicitly asks to regenerate, redo, re-create, or fix a specific image.
- If older wording in this file implies failure, mandatory regeneration, final qualified versions, or regeneration notes, interpret it only as an optional manual review record, not as an execution instruction.

## Optional Demand And Strategy Checks

Optional per-set checks:

- Whether the `Buyer Demand Map` covers six to ten real purchase questions when enough information is available.
- Whether each screen solves a different buyer demand.
- Whether each buyer demand has a matched product selling point.
- Whether every selling point has a confidence level: confirmed, reasonable inference, or needs confirmation.
- Whether standard / functional products emphasize function, structure, operation, efficiency, comparison, and confirmable parameters.
- Whether non-standard / aesthetic products emphasize design, material, beauty, style fit, pairing, and atmosphere.
- Whether consumables emphasize taste, texture, preparation, package convenience, usage frequency, and visible / provided ingredient information.
- Whether gift / emotional-value products emphasize packaging, ceremony, recipient scenario, display value, and atmosphere.
- Whether platform style is adapted when the user specifies Taobao, Tmall, JD, Pinduoduo, Douyin, Xiaohongshu, or Amazon.

## Optional Per-Image Checks

Optional per-image checks:

- Whether the image solves one clear buyer demand.
- Whether the copy states one direct selling point instead of vague mood language.
- Whether every screen has a main title and a selling-point label or short phrase.
- Whether any image is a pure visual with no copy; decorative text, garbled text, or blank labels do not count as valid selling points.
- Whether visual evidence supports the selling point: scene, comparison, action, material macro, structure detail, process flow, information card, ingredient / texture setup, or style pairing.
- Whether text is readable, misspelled, broken, garbled, or too small.
- Whether on-image copy is too long or becomes a paragraph.
- Whether the page uses vertical `3:4`, and whether all detail-page images in the set share consistent dimensions.
- Whether fake logos, garbled text, watermarks, fabricated certifications, ratings, reviews, sales, discounts, efficacy, or parameters appear.
- Whether unconfirmed accessories or functions appear, such as `USB charging`, `4 Pack`, `waterproof`, `dentist approved`, unprovided compatible model, or unprovided material composition.
- Whether high-risk efficacy appears, such as treatment, whitening, weight loss, blood-sugar reduction, detoxification, or medical improvement.
- Whether page structures repeat so much that they feel like the same main image with changed titles.
- Whether design strength is present: clear category motif, varied composition scale, scene density, and visual devices that support selling points.
- Whether the set collapses into the template of same-color gradient background, top-left title, centered product, and small labels.
- Whether each image matches its page role; ingredient, craft, scene, detail, specification, comparison, and process pages should not blur into one generic page type.

## Product Consistency And Physics Review

Product consistency and physical logic can be manual review items. Record the following only as optional review items; do not automatically regenerate affected images:

- The product becomes a different style in the same category, or core silhouette, proportions, or open/closed state changes noticeably.
- Direction, position, or size relationships of patterns, logos, nameplates, labels, or visible copy drift noticeably.
- Key structural parts are added, removed, or repositioned, such as handles, knobs, ports, caps, bases, straps, seams, buttons, or decorative parts.
- Color ratio, material appearance, or visible textures such as transparent, metallic, textile, paper, liquid, powder, food, or creamy surfaces differ noticeably from the original image.
- A detail-page crop does not look like it came from the original product and instead resembles a different product's detail.
- A scene page changes product shape, decoration, pattern, or relative position to fit the environment.
- Product scale is implausible relative to hands, body, table, shelf, pet, bag, cup, room, or other scene objects.
- Contact, shadow, occlusion, perspective, gravity, reflection, or light direction is physically contradictory.
- Hand grip, wearing fit, model pose, pet interaction, or product placement is twisted, floating, misaligned, or impossible.

If the user explicitly asks for regeneration, only change the user-specified affected images and state the correction direction in the delivery notes.

## Design Strength Review

Record the following only as design review items; do not automatically rewrite prompts or regenerate affected images:

- The full set has fewer than five page structures or fewer than three subject scales.
- Most pages have highly similar title positions, product angles, or background systems.
- Category motif is missing, such as sports/outdoor pages without speed or scene, food pages without appetite or ingredients, beauty pages without texture or light, or electronics pages without structure and technical order.
- Visual devices are only decorative and do not explain the selling point or address purchase concerns.
- The opening page lacks an immediate purchase reason, or the closing page lacks a summarizing feel.
- The design looks like a generic template even if copy and product consistency are acceptable.
- The page is high-density but lacks readable hierarchy.
- The scene does not relate to the buyer demand or matched selling point.

If the user explicitly asks for regeneration, prioritize strengthening buyer-demand clarity, subject scale, scene realism, local evidence, comparison relationships, composition direction, accent color, physical logic, and visual devices.

## Allowed Text

On-image text may only come from:

- information explicitly provided by the user;
- facts visible in the image;
- conservative general usage scenarios;
- specifications or packaging information clearly marked as confirmable.

Do not let the model add by itself:

- package quantity, set quantity, capacity, or dimensions;
- material composition, certifications, or test reports;
- dentist recommendation, safety certification, or medical effects;
- sales, ratings, reviews, or brand authorization;
- unprovided charging method, waterproof rating, battery parameters, or compatible model.

## Optional Review Rule

Record the following only as optional review items. Do not automatically regenerate or mark images as failed:

- The image includes unconfirmed facts.
- The aspect ratio is not vertical `3:4`, or dimensions are inconsistent across the detail-page set.
- Any screen lacks a main title or selling-point label/short phrase.
- Copy is only decorative text, garbled text, or blank labels and does not form a clear selling point.
- Key product appearance drifts noticeably, or product shape, pattern, logo/nameplate, decoration, structure, or positional relationship is inconsistent.
- Fabricated certifications, ratings, reviews, efficacy, parameters, or material composition appear.
- Copy is garbled or unreadable.
- Multiple layouts repeat heavily and cannot form detail-page rhythm.
- Design strength is insufficient to show buyer-demand rhythm, category motif, scene evidence, or page structure.
- Physical space logic is wrong, such as floating product, impossible hand grip, contradictory shadow, wrong scale, or impossible wearing fit.

The following issues must not be fixed through local post-processing. Record them only as optional review items by default unless the user explicitly asks for regeneration:

- Typos, broken characters, or garbled copy.
- Insufficient design strength or repeated layout.
- Untrustworthy product details or details that do not look like the original product.
- Physical-logic problems or product drift.

If the user explicitly asks for regeneration, only change the user-specified affected images and do not regenerate the full set unnecessarily.

## Final Delivery

The final response includes:

- generated file paths for delivered images;
- Markdown image previews for delivered images;
- delivered image count;
- `Optional Review Items / Items Needing Confirmation`;
- any information still requiring user confirmation.

If the user explicitly requested regeneration and new images were generated, resend the delivered images in the final response. Do not use final qualified version, problem image, or rejected image as an automatic filtering mechanism.

If local file paths are missing or image previews are not shown, delivery is incomplete.
