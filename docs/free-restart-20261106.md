# Failed free-trial restart — 2026-11-06

A zero-delivery failed fast replica kept the welcome entitlement bound to its source job. The original uncertain-outcome fence disabled the only retry button, leaving the customer unable to use the remaining trial. Ordinary credits were settled at zero.

The welcome endpoint now reports `recoverable`, one remaining task and the original session. The result screen offers an explicit free new generation, preserves original inputs and plan, and removes the misleading paid estimate on an existing free session. Failed replica task details link to that workbench. No customer generation is dispatched by reads or deployment.

`POST /replica/sessions/{document_id}/jobs/{job_id}/restart-free` requires `confirm_new_generation: true`, ownership, a bound welcome grant, a failed/cancelled current fast job, zero successes and zero deliverable outputs. It keeps the original grant/source lineage and the existing three-child recovery bound. Normal retry remains fenced; a server-owned authorization permits only this explicit new generation to replace an uncertain private invocation receipt. The old receipt is retained for audit. This action deliberately does not fetch old outputs.

The source job lock and deterministic retry identity make repeated or concurrent clicks converge on one child job and zero-credit reservation. Existing pricing, outbox dispatch and settlement code remain authoritative. The customer receives a replacement of the same free task, not spendable wallet credits or a second trial with unrelated inputs.

Validation: synthetic route tests cover one and ten references, initial failure, no implicit image calls, explicit free restart, successful delivery, unchanged failed source, preserved receipt, zero reserved/settled credits, duplicate clicks, denied spoof/paid access and bounded repeated failure. An isolated PostgreSQL test exercises four simultaneous clicks. Frontend tests cover eligibility and retained unknown-outcome fences. Production candidates use the existing full payment, rewards, membership, replica and provider regression suite against a disposable database.

Release tooling in `deploy/free-restart-20261106` layers the fix on the already deployed provider-failure release, verifies all application source hashes and preserves schema 20261106_0040, runtime settings and campaign starts. Activation requires healthy candidates, no active customer tasks, a validated database backup and automatic rollback on verification failure.
