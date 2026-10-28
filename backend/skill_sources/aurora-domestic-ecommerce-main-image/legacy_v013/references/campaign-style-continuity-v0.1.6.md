# Campaign Style Continuity v0.1.6

## Purpose

Keep one visual campaign across five different layouts.

## Two-stage contract

### Planned tokens

Before generation, compile concrete engineered defaults from current-product facts, category routing and campaign preferences:

- display, body and numeric font roles;
- headline fill, outline and shadow policy;
- palette roles and usage ratios;
- graphic-device geometry and count limit;
- information-surface fill, radius and border policy;
- photography contrast, lighting, material and background finish.

Do not place product identity, visible copy or layout fields in this contract.

### Calibrated tokens

After the user approves one or more generated pages, extract only the visible style system and bind those files as `STYLE_REFERENCE_ONLY`.

Allowed authority:

- typography;
- color roles;
- shared graphic geometry;
- information surfaces;
- lighting;
- material finish;
- photography finish.

Forbidden authority:

- product color, structure, silhouette or components;
- page layout, anchor, scale, axis, title position or card topology;
- visible copy, claims, offers or other facts.

Original current-product references remain the sole product-identity authority.

## Sequential generation

1. Generate the opening identity and feature pages from original references.
2. Audit identity, facts and design.
3. Ask the user which successful pages establish the campaign style.
4. Calibrate the token contract only from approved pages.
5. Generate each later page from original product references plus the calibrated style contract.
6. Audit immediately; do not let a drifting page become a later reference.

## Drift and repair

Classify visible failures:

- `TYPOGRAPHY_STYLE_DRIFT`;
- `PALETTE_ROLE_DRIFT`;
- `GRAPHIC_LANGUAGE_DRIFT`;
- `PHOTOGRAPHY_FINISH_DRIFT`;
- `LAYOUT_OVERCLONED`;
- `PRODUCT_IDENTITY_DRIFT`;
- `TEXT_FACT_DRIFT`.

For style-only drift, preserve:

- page role;
- communication goal;
- evidence representation;
- layout archetype;
- product identity.

Change only typography, palette roles, graphic devices, information surfaces or photography finish.

For identity or fact drift, do not apply a cosmetic repair. Rebind original evidence and regenerate or block.

## Final-set rule

The final set manifest may contain only pages whose visible-image audit passes. Keep rejected and superseded files in an exclusion ledger with explicit reasons.
