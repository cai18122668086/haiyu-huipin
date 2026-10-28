---
name: aurora-domestic-ecommerce-main-image
description: Continuous-session domestic e-commerce six-image executor for one Taobao search cover plus five carousel images. It accepts user-provided information as factual authority without forcing every fact onto the canvas, gives a concise six-page selling strategy, preserves product identity and campaign continuity, leaves composition and visual storytelling open to expert judgment, audits only material defects, asks only for plan approval and final-set review, and permits at most one repair per page. Use when Codex must plan, generate, inspect, repair, revise, and finalize an expressive six-image Taobao campaign. Always use scripts/harness.py as the only public runtime entry.
---

# 极光国内电商主图

Use `scripts/harness.py` as the only public runtime entry. Do not invoke files
under `scripts/runtime/` directly. Treat every nonzero exit as a hard stop.
In command examples, `python` means a real Python interpreter, not the Windows
Apps execution alias. In Codex Desktop on Windows, if `Get-Command python`
resolves under `Microsoft\WindowsApps`, load the workspace dependencies and use
the returned bundled Python executable for every harness command. Keep text
files and validation in UTF-8 mode; this package configures harness output as
UTF-8 and bundles its Python dependencies under `vendor/`.

This version preserves the formal v0.1.13 identity, USER_FACT, evidence and
copy-integrity chain inside `legacy_v013/`, but its visual defaults are not sent
to the image model unchanged. The public `compile-v013` command imports the
structured legacy contracts, then runs the v0.1.17 quality engine: specific
product form and mechanism outrank broad category words; user preferences must
change the actual style tokens; search hooks are ranked; every page receives a
visual-performance contract; and one concise conflict-resolved execution
prompt is compiled. v0.1.17 also creates an immutable `Campaign Constitution`
plus an `Approved Page Execution Contract`. They freeze product truth, page
duty, selected selling-point meaning, campaign continuity and the required
buyer takeaway; they do not freeze a pixel blueprint. Camera, staging,
composition, scale, lighting drama, copy phrasing and supporting visual devices
remain available to the executing Agent. Do not invoke legacy
scripts directly.

## Single precedence model

Resolve all rules once, before generation:

1. product identity and user-provided factual meaning/numbers/conditions;
2. the current v0.1.17 page duty, lead benefit and campaign visual world;
3. the executing Agent's visual judgment;
4. legacy v0.1.13 outputs as fact/evidence inputs only.

Legacy keyword limits, literal-copy requirements, layout prescriptions and
prompt envelopes never override v0.1.17. Normalize legacy input shapes inside
the adapter. If a legacy stage conflicts, discard only the conflicting legacy
instruction and continue silently; do not expose compiler conflict narration,
do not ask the user, and do not replace the route with a larger manual contract.

## Boundaries

- Use original same-product evidence as the sole product-identity authority.
- Treat every product parameter, selling point, price, promotion, service fact
  and usage claim actively supplied by the user as
  `USER_PROVIDED_DISPLAY_FACT`. Accept it without asking the user to verify it
  again. This makes the fact eligible for selection; it does not require every
  fact or the user's full sentence to appear on a canvas. Select what best sells
  each page. Preserve the meaning, numbers and conditions of selected facts,
  while allowing concise commercial rewriting. Only clearly illegal/dangerous
  content is blocked. Remove claims invented by the model automatically.
- Treat source-photo backgrounds as capture noise unless the user explicitly
  requests preservation. Rebuild the scene without redesigning the product.
- Freeze one concrete current-product Campaign Style before generation.
- Treat the user-approved six-page plan as a strategic contract, not a traced
  layout. Preserve page role, selected lead selling point, intended buyer
  takeaway, product identity, campaign world and any truly mandatory offer or
  structure. Permit the executing Agent to improve shot, composition, crop,
  scale, spatial relation, lighting, copy phrasing and visual device whenever
  the change makes the selling point clearer and more compelling.
- Make selling-point assignment visible but concise before authorization. Every
  plan row shows a concise human-readable label for the page's main selling
  point, related supporting points, one short visual-proof description and a
  non-repetition reason. Preserve the exact user source text internally. Do not
  print every input fact or a full coverage map unless the user asks.
- One image may display multiple related selling points. “One page duty” is not
  “one selling point.” Preserve useful verified information bars and supporting
  facts when hierarchy is clear. Across the six-image set, prevent consecutive
  pages from repeating the same selling-point bundle, proof object and
  conclusion; supporting overlap is allowed when it serves a visibly different
  page duty.
- Treat every user correction as a local delta inside the active session. Never
  call image generation directly from a conversational correction, even after
  finalization. First use `apply-user-adjustment`, then `generate-next`.
- Rehydrate a concise Campaign Constitution summary (identity, campaign world,
  six page duties and source-background isolation) into every image call. Keep
  full internal contracts in execution state; never paste all six page contracts
  or raw JSON into the image prompt. A later user message never replaces the
  active session.
- Translate the lead selling point into one strong visual idea. Prefer real
  product, component, material, action, state, result or credible use context,
  but allow controlled commercial dramatization, sensory atmosphere, scale
  contrast, motion, before/after relation and visual metaphor when they clarify
  the benefit without inventing a factual claim.
- On a multi-claim page, one coherent hero story may carry several related
  selling points. Supporting points are eligible, not mandatory checkboxes;
  omit any that weakens the image. Text and icons may complete meaning, while
  the picture must communicate the lead benefit rather than merely label it.
- Make the lead product-benefit relationship first or joint-first visual. Judge
  whether a thumbnail viewer grasps the benefit and feels its value, not whether
  every sentence can be forensically proven after all copy is hidden.
- Keep style and layout separate. Share style tokens; vary all six shot keys.
- Freeze one `background_style_lock` from the approved Campaign Style. All six
  pages must share recognizable palette roles, material/graphic language,
  typography character and photography finish. Scene content, tonal emphasis,
  light angle, depth, camera and composition may vary by page duty. Consistency
  means one art direction, not six copies of one studio setup.
- Put the full `product_identity_lock`, not only its hash, into every generation
  request. Preserve silhouette, proportions, color blocks, packaging layout,
  component count, connections, opening structure and key labels across all six
  pages and repairs. Selling-point expression never authorizes product redesign.
- Never use a generated page as product evidence. Use only an Agent direct-
  pixel-audited accepted page as `STYLE_REFERENCE_ONLY`.
- Compilation never authorizes generation. Obtain one explicit user
  confirmation and record it with `authorize`. That authorization covers the
  approved plan, Agent pixel audits, scoped repairs and Agent style calibration;
  do not ask the user to perform routine QA.
- Generate sequentially. Never request page N+1 before page N passes its
  visible-pixel gate.
- After a failed first attempt, diagnose every failed check and root cause in
  one repair plan, then allow exactly one automatic second attempt. Repair the
  clean first draft as `REPAIR_CANVAS_ONLY`; keep source photos as identity
  references, never as the second-attempt canvas. After the second attempt,
  select it for final review, record any residual defects, and continue the
  remaining pages without asking the user.
- Audit proportionally. Multiple selling points, useful information bars, or a
  minor aesthetic preference never trigger repair by themselves. Spend the one
  automatic repair only on material identity/fact errors, unsupported claims,
  hard visual errors, source-background leakage, materially broken page role or
  approved plan, or missing visual proof for the page's core renderable claim.
- User interaction has exactly two planned checkpoints: approve the compiled
  six-page plan, then review the complete six-image set. Between them, do not
  ask about facts, compiler fallback, audit results, repairs, baseline scores,
  style calibration, or whether to continue.
- Keep compiler compatibility work silent. Normalize rich original-reference
  objects to path strings inside the v0.1.13 adapter; keep identity descriptions
  in the identity contract. Never ask the user to rewrite this internal format.
- Interpret legacy `all_pages_limit_keywords_to_one_or_two` only as a
  typography-emphasis rule: each page enlarges one or two phrases. It does not
  limit selling-point count, require every fact to be displayed, or prohibit
  concise copy rewriting. If a legacy stage still fails, remove that legacy
  constraint inside the adapter and continue the v0.1.17 route silently. Show
  only the finished concise plan—not compiler errors, fallback narration,
  “exact claim matching” work or implementation details.
- A receipt, file, hash, schema pass, or prompt test never proves visual quality.
- Keep `profile01_semantics_read=false`.

Read [core-policy.md](references/core-policy.md) before starting. Read
[continuous-session-and-repair-policy-v0.1.17.md](references/continuous-session-and-repair-policy-v0.1.17.md)
before any generation, repair, user correction, or post-final revision. Read
[commercial-orchestration.md](references/commercial-orchestration.md) before
compilation; its named decision objects are the single commercial control layer
between product truth, the preserved v0.1.13 compiler and generation. Read
[visual-decision-priors.md](references/visual-decision-priors.md) when building
the identity and six-page plan. Read
[visual-audit-rubric.md](references/visual-audit-rubric.md) before judging any
generated page. Read
[validated-commercial-baseline-v0.1.16.md](references/validated-commercial-baseline-v0.1.16.md)
and [search-cover-commercial-grammar-v0.1.16.md](references/search-cover-commercial-grammar-v0.1.16.md)
before compiling or judging the search cover. Read
[durable-goods-prior.json](references/durable-goods-prior.json)
only when the user explicitly opts into its durable-goods experimental grammar.

Do not read bundled `legacy_v013/` visual references as current Agent
instructions. The adapter/compiler consumes them internally for preserved facts
and evidence. Loading them into the executing Agent recreates conflicting
keyword, layout and literal-copy authorities that v0.1.17 has already resolved.

## Closed workflow

1. Start a session:

   `python scripts/harness.py start --input <raw.json> --session <session.json>`

2. Inspect the original images, build a current-product identity contract, then
   lock it:

   `python scripts/harness.py lock-identity --session <session.json> --identity <identity.json>`

3. Use the preserved v0.1.13 fact/identity compiler plus the v0.1.17 quality
   engine as the default planning path. It automatically adapts the locked
   input and identity, runs the preserved v0.1.1→
   v0.1.7→v0.1.11→v0.1.13 chain, verifies every inherited set/page gate,
   preserves the structured evidence contracts, then replaces category-default
   visual execution with a current-product `Style Evidence Contract`, ranked
   search hooks, a `Visual Performance Contract`, a `Selling Point Visual Proof
   Contract`, an `Approved Page Execution Contract`, and a concise normalized
   prompt. It also builds a machine-checked `Information Confidence`, `Buyer
   Demand Map`, `Demand-to-Selling-Point Match`, `Purchase Decision Type`, `Page
   Strategy`, six-row `Page Task Table` and `Visual Rhythm Plan`:

   `python scripts/harness.py compile-v013 --session <session.json> --output <full-v013-compiled.json>`

   Supply `--input <native-v013-raw.json> --identity <native-v013-identity.json>`
   together only when an already reviewed native v0.1.13 contract exists.
   Otherwise omit both and use the automatic adapter. The bundled source
   manifest is the default; an explicit `--source-manifest` is an expert-only
   override.

   Before authorization, show a concise six-row plan with these mandatory
   columns: `页码/职责 | 主卖点 | 辅助卖点 | 画面证明 | 构图与主体 |
   阅读路径/文字层级 | 不重复理由 | 商业闭合`. Keep each cell to one short
   sentence or phrase; use concise labels instead of pasting long source facts.
   One page may contain multiple related selling points.
   Do not append the internal `selling_point_coverage_map`, confidence table,
   deferred-fact list, claim routing or prompt contracts unless the user asks.
   The detailed machine-readable assignments still drive generation and audit.

   Use the following manual v0.1.17 plan route when the user explicitly
   requests a hand-authored plan or the full compiler returns `BLOCKED`.
   Switch automatically and silently; do not ask permission or narrate compiler
   fallback. Before layout, classify
   every claim's visual observability and choose the product-form adapter,
   product-derived campaign thesis, evidence topology, no-copy proof contract,
   scored view, reading path, anti-template decision, and generation method.
   Complete-product identity pages must declare one same-product image as
   `PRODUCT_PRESERVING_EDIT` target. Then compile the manual plan:

   `python scripts/harness.py compile --session <session.json> --plan <plan.json>`

   Compilation blocks generic blue-gradient rationales, repeated equal-card
   shells, unclassified claims, ungrounded evidence, identity-sensitive pages
   that do not use product-preserving edit, missing current-product style cues,
   missing search-hook ranking, missing performance/approved-execution
   contracts, renderable claims that permit text-only fallback, and known
   cross-product style contamination.

   The commercial orchestration layer must give the search listing cover and
   carousel identity hero different layout structures, shot/crop intent, copy
   topology, reading paths and commercial duties. Retain the exact v0.1.13
   output in the session as evidence, but send only the concise v0.1.17
   conflict-resolved execution prompt to the image model.

   The compiler may retain detailed bundle and evidence fields internally, but
   the user-facing plan must summarize them as main selling point, related
   supporting points and one visual-proof sentence. Keep a lead claim for
   hierarchy, not as a one-claim limit.

4. Return the compiled plan for prompt-only review. Stop until the user clearly
   authorizes image generation once. Record the user's words:

   `python scripts/harness.py authorize --session <session.json> --approval-text <verbatim-user-confirmation>`

   After authorization, own the uninterrupted six-page quality loop. Do not
   show drafts, question user-provided facts, ask whether to repair, or ask
   whether to continue. The next user checkpoint is the complete final set.

5. Request only the next permitted page:

   `python scripts/harness.py generate-next --session <session.json> --out <generation-request.json>`

   Treat this command as the only generation adapter contract. The returned
   JSON is execution state; use its `prompt` as the image-generation brief and
   pass the referenced images. Do not paste raw contracts, hashes, other page
   contracts or full JSON into the image prompt. The request retains complete
   identity, continuity and audit data, while `prompt` contains one concise
   current-page brief: hard locks, selected facts, intended benefit, campaign
   world and explicit creative degrees of freedom. Rehydrate that concise brief
   on every call. Do not pass only the newest user sentence or discard the
   active session. Raw
   image-tool calls outside this step are forbidden. For an automatic repair,
   `edit_target` must be `repair_canvas` from the failed first draft. Original
   references remain `PRODUCT_IDENTITY_AUTHORITY` only; never reuse their full
   desk, wall, keyboard, clutter, seam, shadow or capture edge as a canvas.
   Use the internal `selling_point_assignment` to understand priorities, not as
   a requirement to render every listed item. The lead benefit must be first or
   joint-first; include related supporting points only when they strengthen one
   coherent commercial picture.
   Enforce its square `output_surface_contract` and minimum 1024×1024 size.
   Then bind the resulting file:

   `python scripts/harness.py record-generation --session <session.json> --token <token> --image <image>`

6. View the actual generated pixels yourself. Write an Agent judgment conforming to
   `schemas/visual-judgment.schema.json`, including observed evidence for every
   check, then run:

   `python scripts/harness.py audit-current --session <session.json> --judgment <judgment.json>`

   First record estimated product occupancy, headline visual rank, physical
   evidence actually seen, visible depth layers, commercial closure,
   background competition and thumbnail scan order. Then inspect product
   identity, factual copy, typography/legibility, headline treatment, palette
   roles, graphic language, photography finish, information surfaces, page
   role, evidence relation, product separation, numeric hierarchy, controlled
   design intensity, visual performance, baseline performance, strategic plan
   fidelity, Campaign Constitution continuity, source-background leakage,
   selling-point clarity, desire/impact, claim-to-picture relation, layout
   cloning, set diversity and thumbnail success. A different camera or stronger
   composition is not plan drift when page duty and buyer takeaway remain intact.
   Treat minor shortcomings as advisory; only material
   failed checks enter `all_observed_deficiencies` with a visible problem, root
   cause and required change, then set
   `audit_completeness_confirmation=true`. A
   compiled contract, hash or generation receipt cannot answer any pixel check.
   Never ask the user to judge routine product proportion, commercial density,
   typography, pointer accuracy, page role or thumbnail success.

7. After page 0 passes the ordinary pixel audit, compare it against the
   transferable eight-axis commercial baseline and record the comparison:

   `python scripts/harness.py calibrate-search-baseline --session <session.json> --observation <baseline-observation.json>`

   Score product dominance, headline performance, physical evidence, spatial
   depth, commercial closure, thumbnail competitiveness, commercial-frame
   structure and type hierarchy. Their mean must be at least 80 and no axis may
   be below 65. Multiple verified selling points or useful information bars are
   not baseline failures. Only a material failure returns page 0 to layout/
   composition repair instead of allowing a weak cover to seed the set.

8. On `REPAIR_REQUIRED`, keep the prescribed repair scope. Before the only
   automatic repair, prepare one complete diagnosis covering every failed
   check, visible problem, root cause and required change, plus passed layers to
   preserve, an overall reflection, one coherent second-attempt strategy and
   the expected visible change. Use `schemas/repair-plan.schema.json`:

   `python scripts/harness.py prepare-repair --session <session.json> --repair-plan <repair.json>`

   The executor rebases the original authorization and returns to `AUTHORIZED`;
   regenerate and re-audit once. It never permits a second automatic repair.
   If the second attempt still has defects, record them in
   `residual_issues_for_user_final_review`, select that attempt, and continue to
   the next page. Do not interrupt the user.

   If the user spontaneously gives a midstream correction, or asks to change one page after
   finalization, convert their exact instruction into
   `schemas/user-adjustment.schema.json` and run:

   `python scripts/harness.py apply-user-adjustment --session <session.json> --adjustment <adjustment.json>`

   This reopens only the named page, supersedes its previous accepted/final
   asset, keeps all other accepted pages, and preserves the Campaign
   Constitution. Then use `generate-next`; never call image generation from the
   user message itself. The user-adjusted result is one attempt; if it fails,
   stop again for user intervention. Identity, verified facts, campaign-wide
   style or page-role changes are not local deltas and require replan/restart.

   If the problem is the page system or campaign direction rather than a local
   defect, do not disguise it as a repair. Before any page has been accepted,
   replace the complete six-page plan and invalidate all prior authorization:

   `python scripts/harness.py replan --session <session.json> --plan <new-plan.json> --reason <why-the-system-changed>`

   If one or more pages were already accepted, use a full restart. It preserves
   history and files, supersedes accepted assets, clears calibration,
   invalidates authorization and returns to `IDENTITY_LOCKED`:

   `python scripts/harness.py restart-campaign --session <session.json> --reason <why-the-campaign-is-invalid>`

9. After the first two accepted pages, the executor stops at
   `AWAITING_AGENT_STYLE_CALIBRATION`. Directly compare their pixels, record
   concrete observed evidence and calibrate only approved style fields:

   `python scripts/harness.py calibrate-style --session <session.json> --observation <style-observation.json>`

   The executor rebases the original authorization and continues. This is an
   Agent QA action, not a user approval checkpoint.

10. When all six pages have completed their maximum two-attempt cycle, hide filenames/order and compare the
    six actual images together. The search cover must be uniquely identifiable
    from its identity/header, dominant product-hook field, physical proof,
    commercial closure and type hierarchy; every carousel page must have a
    concrete reason it is not the search cover:

   `python scripts/harness.py audit-set-role-distinction --session <session.json> --observation <set-role-observation.json>`

   Use `schemas/set-role-distinction.schema.json`. Record any remaining role
   ambiguity for final review; do not start another automatic repair and do not
   ask the user until the complete set is ready.

11. Present all six images together in `AWAITING_USER_FINAL_REVIEW`, including a
    concise disclosure of any residual issues. The user now performs the second
    and final planned checkpoint. Only after explicit final approval, finalize:

   `python scripts/harness.py finalize --session <session.json> --output <manifest.json> --approval-text <verbatim-final-user-approval>`

Use `inspect` at any time to read the allowed next actions:

`python scripts/harness.py inspect --session <session.json>`

## Technical enforcement boundary

This executor rejects illegal state transitions, stale authorizations, stale
generation tokens, skipped pages, unreviewed pixels, invalid repair scopes, and
incomplete final sets. Each generation request also carries the approved page
contract and its digest, so the ordinary workflow cannot silently lose the
approved plan between planning and generation. v0.1.17 also gives finalized
sessions a controlled page-reopen path, removing the previous reason to fall
out of the Skill after user feedback. An ordinary Skill still cannot physically
prevent an Agent that has raw access to a separate image-generation tool from
bypassing the executor. For
non-bypassable operation, expose generation only through a plugin, MCP tool, or
service that validates this executor's generation token.
