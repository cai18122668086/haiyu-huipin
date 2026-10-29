# Workflow states

`INPUT_REGISTERED → [INSPECT_INPUTS for mixed uploads] → ROUTE_CLASSIFIED → REFERENCE_DECOMPOSED → VISUAL_PASSPORT_READY → PRODUCT_FACTS_READY → PLAN_AUDIT_REQUIRED → GENERATION_ALLOWED → AUDIT_REQUIRED → (REVISION_ALLOWED → GENERATION_ALLOWED)* → ACCEPTED → FINAL_AUDIT_REQUIRED → FINALIZED → DELIVERED`.

Mixed uploads remain blocked at `INPUT_REGISTERED` until every image has a hash-matched role review with one product and one benchmark role. Explicit role flags set the confirmation at registration.

Detail groups use the same unit states independently and may enter `GROUP_FAILED_CONTINUE` after their one revision. Failed groups cannot pass strict finalization. Only the declared route contract determines canvas, count, order, safe zones and export.

`init-reference-analysis` and `crop-reference-panels` prepare evidence while remaining at ROUTE_CLASSIFIED. `compile-reference-crops` advances only after real full-panel evidence and separate sanitized style crops validate. It can be retried at REFERENCE_DECOMPOSED when repairing boundaries before Passport. Passport stays at REFERENCE_DECOMPOSED on failure, so its analysis can be corrected and retried; no state edit or rerouting is needed.

GENERATION_ALLOWED may mean either plan-ready or a request awaiting artwork: consult the active group's/unit's status and next_action. An awaiting_artwork item keeps its token across resume and blocks any new request. Its request, prompt and bound input hashes are held in `artifacts/recovery-manifest.json`; `resume` validates that lock and `resume-imagegen-request` re-emits the same handoff. Missing or changed files fail closed rather than starting a new request. A revision explicitly increments revision_index, clears the old token, and returns that same target to pending. Default detail groups have two screens each; `generation_scope` makes the distinction between one generated Pair file and two exported screen files explicit.

Native-output receipt preparation does not advance state. A missing or invalid detail receipt leaves the CLI run at GENERATION_ALLOWED/awaiting_artwork with the same token. Non-fixture audit without verifiable original-file evidence fails into the existing one-revision path. Revision clears the prior artwork, provenance and token. A fixture cannot erase a failed real review, including after revision.
