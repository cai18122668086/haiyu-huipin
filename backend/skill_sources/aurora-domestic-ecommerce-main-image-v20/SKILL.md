---
name: aurora-domestic-ecommerce-main-image
description: Plan, generate, inspect, repair, revise, and finalize one Taobao search cover plus five carousel images in a continuous session. Use for domestic e-commerce six-image campaigns that must preserve product identity and user facts while allowing strong page-specific commercial art direction.
---

# 极光国内电商主图 v2

Use `scripts/harness.py` as the only public runtime entry. Treat a nonzero exit
as a blocked transition. The v2 runtime is self-contained and uses only the
Python standard library; it does not call or load historical compiler versions.

Read [core-policy.md](references/core-policy.md) before starting. Read
[six-page-strategy.md](references/six-page-strategy.md) before planning. Read
[visual-strategy.md](references/visual-strategy.md) before compiling prompts.
Read [commercial-performance.md](references/commercial-performance.md) before
compiling or judging any page, especially the search cover.
Read [visual-audit.md](references/visual-audit.md) before judging pixels or
preparing a repair. Read [durable-goods.md](references/durable-goods.md) only
when the user explicitly enables the durable-goods prior.

## Invariants

- Original same-product images are the sole product-identity authority.
- User-supplied product facts are eligible display facts without a second
  verification request. "User-supplied" includes both facts stated in the
  request and clearly readable claims, numbers, endorsements, offers, and other
  copy visible in user-provided same-product images. Before `start`, transcribe
  useful readable image copy into `user_provided_display_facts`; do not guess
  obscured or ambiguous text. In this workflow, `verified_*` means supplied by
  the user, not independently substantiated. Select what best sells each page;
  do not force every fact onto the canvas. Preserve the meaning, numbers, and
  conditions of facts that are displayed. Remove model-invented facts.
- Source-photo environments are capture noise unless the user explicitly asks
  to preserve a named element. Rebuild the commercial scene without redesigning
  the product.
- Freeze one product identity, Campaign Style, six-page strategy, and source-
  background isolation contract before generation.
- The approved plan freezes page duty, lead-benefit meaning, buyer takeaway,
  product identity, visual world, and mandatory commercial conditions. Camera,
  crop, scale, composition, lighting, concise copy phrasing, and credible visual
  rhetoric remain professional execution choices.
- One page may combine related selling points when hierarchy is clear. Avoid
  repeating the same bundle, proof object, and conclusion on adjacent pages.
- Preserve the source claim in the machine contract, but compile a concise
  display-copy contract. A long fact sentence is not a headline. The search
  headline targets 4–10 Chinese characters or equivalent, has a hard maximum
  of 12 effective characters and two lines, and delegates remaining meaning to
  a subheadline or unequal supporting modules.
- Every page uses its compiled layout contract. Shared Campaign Style never
  authorizes reusing product zone, copy zone, dominant view, evidence topology,
  or spatial skeleton from an adjacent page.
- Preserve the pre-v2 commercial-performance floor: dominant product, confident
  headline, real proof, credible depth, unequal information hierarchy, visual
  tension, and verified closure. Do not simplify a page by deleting useful
  numeric, efficacy, specification, service, trust, or condition information.
- Never treat a receipt, hash, file, schema pass, or prompt as visual evidence.
- Every image call must begin with `generate-next`; every result must be bound
  with `record-generation` and directly pixel-audited before progression.
- One issued generation token authorizes exactly one image-tool invocation. A
  running cell, delayed output, missing output hint, display-only result, or
  temporarily unknown file path is not permission to invoke the image tool
  again. Wait on the same call and recover the first completed asset. That
  first completed asset is the page attempt even before it is bound.
- Generate sequentially. Each page has one shared modification slot at most.
  An automatic repair or a user-authorized local adjustment consumes that same
  slot; after either one occurs, that page cannot be modified again.
- The planned user checkpoints are plan approval and complete-set review.
  Routine pixel QA, repair, search calibration, and style calibration belong to
  the Agent.

## Workflow

1. Start and lock identity:

   `python scripts/harness.py start --input <input.json> --session <session.json>`

   `python scripts/harness.py lock-identity --session <session.json> --identity <identity.json>`

2. Compile one normalized v2 plan:

   `python scripts/harness.py compile --session <session.json> [--plan <manual-plan.json>]`

   Without `--plan`, the current compiler creates the plan. With `--plan`, the
   same schema and downstream workflow apply. Compilation never authorizes
   generation.

3. Show a concise six-row plan with:

   `页码/职责 | 主卖点 | 辅助卖点 | 画面证明 | 构图与主体 | 阅读路径/文字层级 | 不重复理由 | 商业闭合`

   Keep exact source facts inside the machine contract. Do not print internal
   confidence, deferred-fact, prompt, or coverage structures unless requested.

4. After clear user approval, record the exact words:

   `python scripts/harness.py authorize --session <session.json> --approval-text <text>`

5. For each page, request the next legal generation, call the image tool with
   the returned execution-rich `prompt` and referenced images, then bind the
   asset:

   `python scripts/harness.py generate-next --session <session.json> --out <request.json>`

   `python scripts/harness.py record-generation --session <session.json> --token <token> --image <image>`

   If the completed call has no exposed path, recover it from the current
   thread's exact generated-image directory:

   `python scripts/harness.py recover-generation --session <session.json> --token <token> --generated-dir <current-thread-generated-image-directory>`

   Use `generation_request.imagegen_reference_paths` exactly; do not silently
   reduce it to one convenient source image. Invoke the image tool once for the
   issued token. If the tool yields `Script running with cell ID ...`, use the
   wait mechanism on that cell until completion. If pixels are displayed or an
   image-generation completion event exists but the file path is not yet in
   the wrapper output, recover that same completed asset from its output hint
   or call-specific generated-image record and bind it. Never rerun imagegen to
   obtain a cleaner return shape, a path, or another candidate. A campaign
   restart is blocked while a generated asset is pending; recovery is the only
   legal continuation after a successful call.

   Enforce 1:1 and at least 1024×1024. Initial identity-sensitive pages use the
   declared same-product edit target. An automatic repair uses the failed first
   commercial draft as `REPAIR_CANVAS_ONLY`, except an identity-destroying or
   impossible-geometry failure, which uses `REBUILD_FROM_PRODUCT_EVIDENCE`.
   Automatic repair and user adjustment share the page's single modification
   slot. This budget is runtime state and must not be included in the visual
   image prompt.

6. Inspect the actual pixels and submit a judgment conforming to
   `schemas/visual-judgment.schema.json`:

   `python scripts/harness.py audit-current --session <session.json> --judgment <judgment.json>`

7. If the first attempt returns `REPAIR_REQUIRED` and the page's modification
   slot is unused, address every material failed check in one repair plan:

   `python scripts/harness.py prepare-repair --session <session.json> --repair-plan <repair.json>`

   A failed automatic second attempt is retained with residual disclosure and
   the remaining pages continue. That page cannot then receive a user
   adjustment. A failed user-adjusted attempt stops in
   `AWAITING_USER_INTERVENTION` and cannot be modified again.

8. After page 0 passes, record the eight-axis search-cover comparison:

   `python scripts/harness.py calibrate-search --session <session.json> --observation <observation.json>`

   After the first two accepted pages, calibrate style from direct pixel
   observations only:

   `python scripts/harness.py calibrate-style --session <session.json> --observation <observation.json>`

9. Convert any later page correction into a local delta before generating, but
   only while that page's modification slot remains unused:

   `python scripts/harness.py apply-user-adjustment --session <session.json> --adjustment <adjustment.json>`

   The adjustment consumes the same slot as automatic repair, so it cannot be
   repeated and cannot follow an automatic repair. Identity, verified facts,
   campaign-wide style, and page-role changes require a campaign restart rather
   than a local delta.

10. When six pages are selected, compare their pixels as a set:

    `python scripts/harness.py audit-set --session <session.json> --observation <observation.json>`

    Present all six images and residual issues together. After explicit final
    approval:

    `python scripts/harness.py finalize --session <session.json> --output <manifest.json> --approval-text <text>`

Use `inspect` at any time. Use `restart-campaign` for a campaign-wide change,
but never to escape `AWAITING_GENERATED_ASSET`.

## Technical boundary

The runtime enforces state transitions, normalized schemas, page order,
authorization freshness, generation tokens, image dimensions, shared per-page
modification budgets,
and final-set completeness. It cannot prevent a caller with raw access to a
separate image tool from bypassing the runtime; non-bypassable enforcement
requires a plugin, MCP tool, or service that validates the generation token.
Within a Skill execution, treat any successful raw image-tool completion as
consuming the current issued attempt whether or not `record-generation` has run.
The only legal recovery is to bind and audit the first completed asset.
