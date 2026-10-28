# Product presence rhythm

Treat product presence as a long-page narrative decision. A product image earns space only when it performs a buyer job that the source, material, action, result, scene or evidence cannot perform better.

## Per-screen decision

Every newly authored compact module declares:

```json
{
  "product_presence_decision": {
    "policy": "ABSENT_PREFERRED",
    "unique_job_if_present": "NONE",
    "primary_subject_without_product": "the source, material, mechanism, result, scene or evidence that proves this screen",
    "identity_binding_without_product": ["verified product-specific material/action/result", "campaign palette or graphic motif"],
    "absence_reason": "why a complete product would dilute or repeat the evidence"
  }
}
```

The runtime mirrors `policy` into `module_visual_expression_contract.product_presence_policy`. Explicit authored decisions survive normalization; missing legacy decisions are routed from screen duty and archetype.

## Policies

- `REQUIRED_DOMINANT`: a complete product is the first visual subject. Use for M01, product/detail/selection/specification decisions and purchase closure.
- `REQUIRED_SUPPORTING`: a complete product participates in or closes an action/result but remains secondary. Do not use it as a generic continuity badge.
- `IDENTITY_TOKEN_ONLY`: no complete product, package, silhouette or packshot. Only an approved product-specific detail, material, part, color or action token may appear.
- `OPTIONAL`: compatibility route when either a small product or absence is equally valid. Do not default to it when one choice is clearly stronger.
- `ABSENT_ALLOWED`: compatibility route that permits absence but does not require it.
- `ABSENT_PREFERRED`: absence is the intended composition. No complete product, package, corner packshot, floating product card, blurred package or background package.

## Cross-category routing

| Buyer job | Default product role | Primary subject |
|---|---|---|
| Establish identity and total value | REQUIRED_DOMINANT | complete product plus core result |
| Show source/origin | ABSENT_PREFERRED | source place, organism, raw material or provenance evidence |
| Explain material/ingredient | ABSENT_PREFERRED or IDENTITY_TOKEN_ONLY | material macro, component relation or source-to-property diagram |
| Explain mechanism/process | ABSENT_PREFERRED | object, action path and visible state change |
| Prove a dedicated benefit | ABSENT_PREFERRED | claim object, transformation or result |
| Show use action | REQUIRED_SUPPORTING only when the product physically participates; otherwise IDENTITY_TOKEN_ONLY | hand/action/contact/result |
| Show lifestyle or sensory result | ABSENT_PREFERRED | person, environment, texture, taste, scent or finished state |
| Compare variants or specifications | REQUIRED_DOMINANT | the products/options being chosen |
| Show trust/document evidence | ABSENT_PREFERRED | certificate, report, facility or documented source |
| Close purchase | REQUIRED_DOMINANT | complete product, core benefit and buyer/use context |

These are defaults, not category templates. Override only when the specific screen has a clear buyer reason.

## Identity without a full product

An absence screen must still belong to this campaign. Use at least one fact-bearing product-specific anchor and one series anchor when available:

- verified source, ingredient, material, component, texture, process, action or result;
- approved partial detail or product-bound tool/contact state;
- campaign palette, light, typography character, graphic line, recurring motif or framing language;
- continuity from the preceding and following screen.

An independent logo is not a substitute for a product-bound identity binding. M02 onward keeps the existing zero-independent-logo rule, and a `BRANDLESS` campaign keeps it on M01 as well, so absence screens lean entirely on verified product evidence and campaign DNA.

## Campaign rhythm

The runtime derives `campaign_product_presence_rhythm` from module decisions. Evaluate the sequence as meaning, not a fixed percentage:

- M01 establishes the complete product;
- middle screens alternate among product, identity token and absence according to proof needs;
- complete-product returns occur at meaningful action, selection, recap or purchase peaks;
- several consecutive full-product screens are a warning when the product performs no new buyer job;
- several absence screens are acceptable when their subjects remain product-specific and the page later returns to the complete product.

Do not enforce mechanical alternation or a universal maximum gap. The failure is purposeless repetition or lost attribution, not a fixed count. v4.3 does enforce one campaign-level ceiling for the strongest role: at most 3 dominant complete-product screens in an 8- or 10-screen page and at most 4 in a 12-screen page. This is an anti-collapse budget, not a requirement to reach the maximum.

`complete_product_role` must agree with the presence policy: `DOMINANT` with `REQUIRED_DOMINANT`, `SUPPORTING` with `REQUIRED_SUPPORTING`, `IDENTITY_TOKEN_ONLY` with `IDENTITY_TOKEN_ONLY`, and `ABSENT` with `ABSENT_PREFERRED` or `ABSENT_ALLOWED`. Only the opening and closure may combine `DOMINANT + HERO_POSTER`.

## Prompt and audit consequences

For `ABSENT_PREFERRED`, explicitly prohibit hidden, corner, background, blurred and decorative product insertion. Adding the product is a contract failure even if the image remains attractive.

For `IDENTITY_TOKEN_ONLY`, a complete product/package is a failure; the approved identity token must be visible.

For absence-favored screens, audit both:

1. whether an unplanned complete product appeared; and
2. whether product attribution remained visible through the planned identity bindings.

At long-page level, inspect repetitive product occupancy, the strength of absence-screen attribution and whether product returns coincide with meaningful purchase peaks.

## Learning from references

Analyze every reference screen separately. Record whether the complete product is dominant, supporting, token-only or absent; name the actual primary subject; explain why attribution survives without a product; and extract only that transferable rhythm. Never copy another brand, asset, copy, watermark or proprietary layout.



## Truthful per-slice identity observations

Use `identity-observation.schema.json` as the only schema authority. Actual `DOMINANT` or `SUPPORTING` presence uses `VISIBLE_COMPLETE_PRODUCT`: shape, colorway and texture are explicit boolean comparisons with named visible features. Actual `IDENTITY_TOKEN_ONLY` uses `VISIBLE_IDENTITY_TOKEN`: name the visible product-bound features and use `NOT_APPLICABLE` for unobservable whole-product properties. Actual `ABSENT` uses `NO_VISIBLE_PRODUCT`: all three comparison fields are `NOT_APPLICABLE` and `visible_features` is empty. This does not declare the planned role satisfied; unexpected absence still fails the exact role and expression gates.

A pair with any observed full product uses the full comparison scope; otherwise any visible token uses token scope; otherwise it uses no-visible-product scope. Do not infer observations from the plan or call an unknown comparison not applicable. A full product seen on a planned absence/token screen must explicitly set `unplanned_full_product_seen=true` and retain the material failure. A bounded identity follow-up cannot turn a known false into true, change known applicability, repair missing atomic observations, or authorize another image render.
