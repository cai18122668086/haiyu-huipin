# Product-operation routing

Use this stage before identity locking and page planning. Its purpose is to decide whether the campaign should organize supplied selling points or build a cautious product strategy from photographs. It does not replace evidence rules.

Every selected primary pain must survive into `campaign_pain_solution_contract`; recording it only in operating analysis is not coverage. Schedule either a dedicated pain module before a verified solution module or one combined pain-solution module. The pain side may use a disclosed operating hypothesis as safe context, while the solution side must cite verified `value_bridge_ids`. Never convert a hypothesized pain into an efficacy claim.

## Automatic route selection

Choose exactly one route without asking the user to name it:

- `USER_SELLING_POINT_LED`: the user supplied at least one concrete product feature, benefit, claim, ingredient, structure, parameter, use advantage, audience advantage or sensory point in text or a separate document.
- `PHOTO_TO_PRODUCT_STRATEGY`: the user supplied product photographs but no usable selling-point information. Generic requests such as “做得高级”“品质好”“帮我做详情页” do not count as selling points.

Text read from packaging inside a product photograph is product evidence, not a user-specified selling-point priority. A run with photographs only remains `PHOTO_TO_PRODUCT_STRATEGY` even when packaging OCR reveals useful facts.

When the user supplies only part of the strategy, use `USER_SELLING_POINT_LED`: preserve those points as priority anchors and use product-operation reasoning only to fill audiences, pains, supporting benefits, proof routes and unanswered buyer doubts. Never replace, weaken or silently omit a supplied point.

## Shared atomic-fact gate

Before either route continues, split every compound statement into atomic meaning units. One fact ID must express one independently coverable proposition. For example, “家用、除菌、除螨、除真菌、茶香、适合手洗” becomes separate facts; assigning one of them to a module never marks the others covered.

For every atomic fact record its exact source, conditions, risk route and display intent. Product claims may come only from `USER_FACT`, `DOCUMENTED_EVIDENCE` or directly observable product evidence. Pain hypotheses and audience hypotheses are not product facts.

## Route A: user-selling-point led

1. Preserve the user's supplied selling-point set as standing coverage authority.
2. Split supplied points into atomic facts and resolve contradictions or claim conditions.
3. For each point identify the likely buyer, pain or doubt, practical benefit, purchase role, visual proof route and suitable page duty.
4. Add supporting opportunities only when they are grounded in supplied materials or clearly labelled operating hypotheses.
5. Rank the supplied points; do not distribute page space equally. Select a small must-remember set for the opening promise, proof body and closing reinforcement.

Before planning screens, write one ledger row for every original supplied point:

- `ACTIVE`: retained directly and mapped to atomic facts/modules;
- `MERGED`: combined only because buyer question, proof relation and commercial conclusion are genuinely shared;
- `QUALIFIED`: retained with a narrower evidence-safe expression;
- `DEFERRED`: unsupported or immaterial after a concrete recorded reason;
- `CONFLICT_BLOCKED`: cannot proceed until a material SKU/source conflict is resolved.

No original point may disappear between intake and approval. A compound point may map to several atomic facts and several modules. A user point with no currently defensible fact IDs stays in the ledger as deferred or blocked rather than being silently dropped.

## Route B: photo-to-product strategy

Inspect every supplied product image directly and perform packaging OCR where legible. Then build an operating analysis:

1. Identify the likely product, category, form, variant, packaging, visible structure and use cues. Preserve uncertainty instead of forcing a precise SKU.
2. Extract atomic observed or documented facts from visible packaging and product construction.
3. Infer plausible buyer groups, situations, jobs-to-be-done, functional pains, experience pains, decision doubts and transaction risks.
4. Build value bridges from evidence to commercial meaning:

   `evidence-backed fact -> product feature -> buyer benefit -> pain/doubt addressed -> purchase reason -> truthful visual route`

5. Generate at least two positioning candidates when the evidence supports materially different directions. Compare evidence fit, pain strength, differentiation, visual potential, commercial usefulness and legal risk. Select one campaign thesis and retain useful secondary angles.
6. Do not turn an inferred pain into a product-effect claim. A scene may express a plausible buyer situation; a statement that the product solves it must return to verified product evidence.

Example: a visible locking lid may support a commute-and-leak-anxiety scenario. It does not support an unshown quantified leakproof or insulation claim.

## Operating-analysis contract

Persist the following before planning:

- selected route and reason;
- user-supplied selling points, if any;
- observed atomic facts and their evidence;
- buyer and scenario hypotheses with confidence and reasoning;
- pain and doubt hypotheses;
- evidence-to-value bridges;
- positioning candidates and selection rationale;
- unsupported opportunities or evidence gaps;
- assumption disclosure stating what may guide scenes but may not appear as factual promotional copy.

The selected target buyer, primary pain, campaign promise and priority claim IDs must enter the plan's `operating_strategy`. The plan must also contain a recall contract stating which verified claims the buyer should remember, which appear in the first-screen value promise, where they receive proof, and which return in the closing module.

## User-facing disclosure

Before the storyboard, show a concise product-operation brief:

`采用路由 | 确认的产品事实 | 用户指定卖点 | 推演人群与痛点 | 推荐定位 | 待验证假设`

Do not expose confidence tables or raw internal JSON. Make it clear which statements are verified product claims and which are operating hypotheses. Combine this brief with the existing storyboard approval unless a material identity conflict or misleading-risk question must be resolved first.


