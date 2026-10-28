# Intake and evidence

## Minimum viable intake

The minimum user submission is one clear current-product photograph. Product name, category, target buyer and selling points may be derived provisionally from photographs through `PHOTO_TO_PRODUCT_STRATEGY`; preserve uncertainty and do not upgrade inference into fact.

Require before planning, whether supplied by the user or produced by the operating-analysis stage:

- product name and category;
- at least one clear current-product image;
- target buyer or primary use context;
- at least one verified purchase-relevant fact;
- desired output platform or an explicit internal-review profile.

Read [product-operation-routing.md](product-operation-routing.md) when the user has not supplied concrete selling points or has supplied only a partial selling-point set.

Accept but do not require:

- multiple product angles and packaging shots;
- specification sheets, labels, test reports, certificates, ingredient tables, service terms, prices, and promotion conditions;
- brand guide and authorized fonts;
- visual references and negative examples.

## Evidence and risk routes

Use four classes:

| Class | Meaning | Allowed use |
|---|---|---|
| `USER_FACT` | User explicitly supplied the fact | Copy and planning, subject to legal qualification |
| `DOCUMENTED_EVIDENCE` | Fact visible in a supplied document or official product label | Copy, comparison, trust and parameter modules |
| `OBSERVED_PRODUCT_EVIDENCE` | Shape, color, parts, texture or visible state observed in product images | Identity and visual proof |
| `UNSUPPORTED` | Inferred, contradictory, unreadable or absent | Exclude; never decorate into apparent proof |

Record exact conditions with every number: unit, test context, applicable model, time range, population, package variant, price period, or service boundary.

Then route each supplied statement as one of:

- `USER_CONFIRMED_FACT`: user-supplied, attributable and suitable for accurate commercial expression;
- `DOCUMENTED_FACT`: supported by label, specification, test, certificate or official material;
- `EXPERIENCE_EXPRESSION`: sensory or scenario language that must remain non-medical and non-absolute;
- `HIGH_RISK_CLAIM`: medical, therapeutic, quantified efficacy, certification, standard or absolute comparison requiring documentation and conditions;
- `CONFLICTING_FACT`: incompatible SKU, quantity, structure or specification statements;
- `UNSUPPORTED_INVENTION`: absent, inferred or fabricated content that must not be shown.

Default question budget is zero. Ask one batched question only when conflicting facts or a material misleading risk cannot be resolved from the supplied evidence.

## Identity contract

Record:

- canonical name, category and variant;
- primary reference image IDs;
- stable silhouette and proportions;
- color and material roles;
- packaging, labels, logos and typography that must remain recognizable;
- count, parts, connectors and included accessories;
- allowed views, open/closed states, installed/uninstalled states and contents;
- uncertain details that must not be invented;
- forbidden mutations;
- forbidden source-background elements.

## Fact selection

Create a coverage ledger for every supplied fact. The ledger prevents accidental deletion; it does not require one screen per fact. Rank each fact by:

`buyer relevance x differentiation x evidence strength x visual potential x legal safety`

One fact ID must represent one atomic proposition. Never let a covered fragment mark the remaining meanings in a compound statement as covered.

Use facts with weak visual potential as concise qualifiers or route them to specification/trust modules. Merge low-value repetition. Defer only conflicts, undocumented high-risk claims, unsupported invention or immaterial duplication.

## Retention and risk routing

Do not discard useful product information merely because it is not directly photographic or because some claims in the category can be risky.

- Retain supplied ingredients, formula, texture, process, use, scenario, specification and feeding/operation information when it is attributable and purchase-relevant.
- Convert low-risk information into accurate commercial language, object labels, diagrams, process scenes or specification modules.
- Add the original condition when a statement is only true for a variant, population, serving, test or use method.
- Route medical, therapeutic, quantified efficacy, certification, standard, test and absolute-comparison claims to documented-evidence review.
- Delete only unsupported, contradictory, misleading or immaterial repetition.

Evidence safety should change wording and proof form before it changes content coverage. If the user says supplied points should be shown, treat that as standing authorization to maximize truthful coverage without repeatedly asking which points to keep.


