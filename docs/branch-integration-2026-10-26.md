# Combined branch integration — 2026-10-26

The release starts from main 95f764a (including PR133). PR132 is merged with its source changes. All 132 remote heads from the recorded inventory are ancestors of the integration commit.

Older branches were checked against the current implementation. Their useful fixes already exist or their runtime/deployment is superseded. Their history is reconciled while the current implementation is retained; obsolete APIs, migrations and frozen Skill files are not reintroduced. This is recorded explicitly rather than claiming old source was newly imported.

| Branch | Resolution | Evidence / replacement |
| --- | --- | --- |
| `codex/aurora-domestic-main-image` | audited history reconciliation (current implementation retained) | Current Aurora six-image planner/provider, v2 Harness, workspace resume, error propagation, square normalization and balanced evidence adapter supersede the August workflow. Keep current source and frozen protocols. |
| `codex/aurora-observed-evidence-fix` | audited history reconciliation (current implementation retained) | Observed-evidence completeness check and calibration normalization already present in the newer provider; current homepage regression tests cover restored CI. |
| `codex/aurora-v20-release` | audited history reconciliation (current implementation retained) | All unique patches already applied (git cherry -). Keep current realtime login/admin implementation. |
| `codex/detail-page-skill-v070-integration` | audited history reconciliation (current implementation retained) | The August v0.7.0 detail runtime and duplicate migration 0100 are replaced by current v4.3.1 native detail runtime, durable job records and deployed migration chain. Development auth deferral and browser API URL support already present. |
| `codex/fix-reference-source-upload` | audited history reconciliation (current implementation retained) | Only patch already applied (git cherry -). Keep current reference-source inference. |
| `codex/inspiration-auth-pool-fix-20261026` | normal merge | PR132 resource isolation: release DB connections before storage/archive I/O; retain source and regression tests. |
| `codex/inspiration-workspace-materialize` | audited history reconciliation (current implementation retained) | Both unique patches already applied (git cherry -). Keep current workspace materialization and automatic aspect ratio. |
| `codex/internal-beta-staging-v11` | audited history reconciliation (current implementation retained) | Same-origin internal proxy and deployment overlay are present in current deploy files. Retain current image pins, credentials policy and runtime layout. |
| `codex/internal-beta-staging-v12` | audited history reconciliation (current implementation retained) | Controlled real-provider switches and deployment preflight are present in current deployment. Retain current production configuration. |
| `codex/internal-beta-staging-v13` | audited history reconciliation (current implementation retained) | Only patch already applied (git cherry -). Keep newer staging overlay. |
| `codex/internal-staging-v10f` | audited history reconciliation (current implementation retained) | Credit release/idempotency/quota toggle already implemented, with newer row locking and accounting-only mode. Current concurrency parser accepts env strings and supports 1..32. Old mock/SSH staging layout is superseded. |
| `codex/internal-staging-v10h-final3` | audited history reconciliation (current implementation retained) | Current tokenized npm lifecycle scanner and ignore-scripts installation already include this hardening. Preserve newer deployment preflight and tests. |
| `codex/local-worker-routing-fix` | audited history reconciliation (current implementation retained) | Connected-worker routing, repeated availability check and account-session synchronization are already in current code; newer lease fencing remains authoritative. |
| `codex/main-image-skill-v2-integration` | audited history reconciliation (current implementation retained) | Current AuroraV20HarnessRunner and locked v2 Skill supply the native integration. Retire the obsolete parallel task API/tables/migration 0007 instead of registering a duplicate runtime. |
| `codex/release-20260820-hotfix` | audited history reconciliation (current implementation retained) | Current style ratio validator, evidence completeness adapter, provider-cost defaults and migration 0010 already contain the hotfix. Keep the newer provider and homepage assets. |

The JSON companion records exact branch heads and source-level evidence. CI must pass on the final combined tree before deployment. The unfinished, uncommitted frontend refresh in the primary checkout is preserved for its owning chat and is excluded from this release.
