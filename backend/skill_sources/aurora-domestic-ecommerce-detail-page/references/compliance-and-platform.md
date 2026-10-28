# Compliance and platform profile

Verified on 2026-08-09. Treat this file as a dated operational reference, not legal advice.

## Legal baseline

- E-commerce operators must disclose product/service information comprehensively, truthfully, accurately and promptly, and must not use fabricated transactions or reviews for misleading promotion. Source: [SAMR, E-Commerce Law, Article 17](https://www.samr.gov.cn/wljys/gzzd/art/2023/art_3e75434c954b4cb9bd14867dacff26d1.html).
- Internet commercial promotion through text and images can fall under the Advertising Law and the Internet Advertising Measures. Source: [SAMR, Internet Advertising Measures](https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/art_d93a579afd45413e8576e4623fab348f.html).
- Do not use absolute or superlative claims as a simple keyword blacklist. Evaluate exact context and evidence, but route risky claims to human/legal review. Source: [SAMR, Enforcement Guide for Absolute Advertising Terms](https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/ggjgs/art/2023/art_64279265c896452f8f638f2de12b8003.html).
- AI-generated images are within the scope of China's AI-generated/synthetic content labeling rules. The measures and GB 45438-2025 took effect on 2025-09-01. Preserve tool-provided provenance/metadata, do not maliciously remove labels, and use the publishing platform's declaration controls when applicable. Sources: [AI labeling measures](https://www.nrta.gov.cn/art/2025/3/14/art_113_70340.html), [GB 45438-2025](https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=F32EA2A561F1886CD8D606513512D547&refer=outter).

## High-risk claim routing

Require documented evidence and exact conditions for:

- medical, health, cosmetic efficacy, weight loss, disease prevention or treatment;
- food nutrition, organic, origin, ingredient proportion and safety;
- pet nutrition and therapeutic implications;
- test results, before/after data, percentages and time-bound results;
- certification, patent, award, standards compliance and authority endorsement;
- lowest price, first launch, best-selling, number one or competitor comparison;
- warranty, return, gift, bundle, price and promotion conditions.

If evidence is absent, remove the claim; do not weaken it cosmetically while preserving the implication.

## Font and asset rights

- Use only fonts with a recorded license or the user's documented authorization.
- Source Han Sans/Serif are available under SIL Open Font License 1.1; keep the license when redistributing font software. Sources: [Adobe Source Han Sans](https://github.com/adobe-fonts/source-han-sans), [Source Han Serif license](https://github.com/adobe-fonts/source-han-serif/blob/master/LICENSE.txt).
- A font installed on a computer is not proof of commercial authorization. The National Copyright Administration has reported cases recognizing protection for original typeface glyphs. Source: [National Copyright Administration case note](https://www.ncac.gov.cn/xxfb/yjdt/201809/t20180926_49346.html).
- Record rights for product photos, models, logos, mascots, certificates, third-party illustrations and reference images.

## Platform specification policy

Public platform documentation is incomplete, dynamic and often subordinate to the merchant backend. Therefore:

1. Never hard-code `1440 px`, `750 px`, `790 px`, one file-size limit or one slice height as universal.
2. Store a target profile with `canvas_width_px`, `module_height_px`, `safe_margin_px`, `slice_height_px`, `format`, `quality`, `max_file_bytes`, `color_space`, and `source`.
3. Prefer the current merchant upload UI and current platform rule center as authority.
4. When an exact public rule cannot be verified, set `verification_status` to `BACKEND_CONFIRMATION_REQUIRED`.
5. Use `INTERNAL_REVIEW_1440_3_4` only as a high-resolution working master, never as proof that a platform accepts that exact width.

## v4.1 pair-canvas decision gate

Choose the campaign output container before session start. Continue an explicit approved profile only inside the same active product campaign; a new product or campaign must not inherit it. Evaluate:

- target backend or user-specified delivery requirement;
- mobile viewport legibility and likely first-fold crop;
- product silhouette and the amount of vertical scene needed;
- visual-proof type: hero, process, comparison, data, scene or specification;
- number of claims that can remain readable without turning the screen into a document;
- generation reliability and clean downstream slicing.

In v4.1 the sole user-facing group canvas is 3:8, rendered as adjacent two-screen pairs. The 3:4 screen window and its pixel dimensions are internal fixed decomposition geometry, not a second ratio choice or approval checkpoint. Delivery slices inherit that fixed window and may not be independently resized or re-planned. If the target backend is unverified and the user did not specify a profile, use `INTERNAL_REVIEW_1440_3_4` as the working window, record `BACKEND_CONFIRMATION_REQUIRED`, and obtain approval once for the complete group→screen packet and 3:8 pair canvas.

The following is an example internal-review profile only:

```json
{
  "profile": "INTERNAL_REVIEW_1440_3_4",
  "canvas_width_px": 1440,
  "module_height_px": 1920,
  "safe_margin_px": 72,
  "slice_height_px": 1440,
  "format": "PNG",
  "max_file_bytes": null,
  "verification_status": "INTERNAL_ONLY"
}
```

Before delivery to Taobao/Tmall, JD or Pinduoduo, replace this profile with values confirmed in that merchant's live backend or current official rule center.


