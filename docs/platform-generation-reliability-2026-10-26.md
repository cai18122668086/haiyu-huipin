# Platform generation reliability

The current exact replica protocol applies to every workspace. The former
`replica_exact_compact_workspaces` setting is accepted for environment compatibility
but no longer gates creation or plan correction. Style sessions retain their
released analysis and direct delivery policy.

The application delivery policy also covers older exact sessions with no compact
binding. A frozen protocol keeps its original identity. The worker retains the
approved native plan, source inputs and actual historical judgments, and writes
a separate `GENERATED` / `NOT_REVIEWED` delivery record. File decoding, hash,
scope, plan order and canvas checks remain required. A retry uses its durable
parent snapshot and does not regenerate existing valid images. A request with
an unknown image outcome is never automatically dispatched again.

Protocol v7 is frozen with its deployed hash. New exact sessions use v8.
Historical job failures remain historical failures; recovery produces a linked
execution and preserves the original images and judgments. Terminal native jobs
can reconcile session history even if submission was interrupted after creating
the single bound job.

Image gateway receipts retain bounded, redacted error code, type, parameter,
message, upstream request ID and actual route/model. Request IDs in response
headers and non-JSON rejection bodies are also supported. The single-image and
masked adapters, replica execution snapshots, usage events and durable failed
attempt/item details preserve the receipt. Credentials, request bodies and image
bytes are not copied into these diagnostics.

An explicit HTTP 400 route/model rejection can use the configured image backup.
Unknown validation errors, content-policy rejections, possible image/async output
and single-dispatch requests do not trigger that fallback. Each configured route
is attempted at most once and successful fallback retains the first rejection
and actual network request count. Previously discarded error messages cannot be
recovered retroactively.

This release integrates the recent replica release lineage, unified platform
optimizations and the pending authentication diagnostics branch into main.
Older August/September experimental branches and preservation snapshots are
outside this integration. Deployment updates application services from the same
verified source tree while preserving their individual environments, queues,
mounts, runtime limits and rollback images.
