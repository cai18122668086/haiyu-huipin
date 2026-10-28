# Style generation and direct file delivery

`replication_method="style"` retains native product/reference intake, physical
panel analysis, safe style crops, facts, route planning, plan audit and ordered
product/reference bindings from `all-category-style-benchmark-still`.

The production provider factory in `execution.py` installs
`ReplicaDeliveryProvider`. Its style override replaces the old sequence
`record → pixel audit → revision → pixel audit → accept → global audit → finalize`
with generation and file delivery. No post-generation AI review or automatic
repair is called. The original Skill remains unchanged. The released exact v5
adapter and its dependencies are archived with their original binding; the
current exact v6 adapter has its own source binding and recovery policy.

The original `workflow.json`, route plan, inputs, facts and historical verdicts
remain immutable. `style-direct-session.json` records `GENERATED` and
`NOT_REVIEWED` separately. The native request compiler and image handoff run in
a separate generation workspace with honest `generated` markers for completed
targets. This preserves sequential compilation without inventing audit passes.

File checks follow the style contract: singles require their native route
dimensions; detail masters use the existing absolute 0.01 ratio tolerance,
equal-height splits, and G01's adopted screen ratio. There is no new minimum
edge or total-call/time budget. Corrupt images, wrong geometry, changed hashes,
missing files, or wrong order/count stop delivery. Detail output uses native
equal-height crops; single output retains original bytes.

Normal HTTP retries require the same workspace, document, confirmed plan,
source version, original assets and facts. Persisted snapshots verify every
JSON and object byte. Known canvases, including returned but unrecorded images,
are reused; an invoked request with unknown outcome cannot be dispatched again.
An explicitly created retry job may request an unfinished target again after a
known provider rejection, retaining the rejected token and all completed images.
The original execution snapshot and AI FAIL/PASS records remain unchanged.
Progress and usage metadata report generated counts and zero reviewed/accepted
images, and reuse the existing UI's “未进行自动审图” presentation.

Synthetic tests cover both native single/pair branches, format failures,
planning gates, immutable history, changed bindings, unknown results, returned
but unrecorded requests, deletion of temporary files, and a real HTTP/worker
retry through durable storage. They do not claim live visual quality.

The style analysis adapter (`replica_style_analysis.py`) verifies generation
reference coverage before page planning. Every observed source content step
must bind a reviewed physical crop from its corresponding source panel. Crop
origin, coordinates and actual bytes are server-recorded. Model-proposed crop
corrections are materialized and their new pixels receive a fresh pre-generation
review; changing flags or relabeling another panel's swatch cannot fill a gap.
The original native crop compiler, factual registration and plan audit remain
mandatory. Full analysis panels still supply global layout evidence, while safe
generation crops supply supplementary type, material and action details.

This addresses an observed failure: a twelve-step live plan had only four safe
crops bound to four steps. Eight steps lacked generation references, while the
old revision loop only rewrote page plans. The immutable native generation
compiler would also reject those missing bindings. Tests reproduce that failure
and verify corrected bindings with the unchanged native compiler.

Failed crop, facts and plan stages retain hash-verified native snapshots and a
style cursor. Normal retry/revision restores original inputs and the failed
stage without repeating completed intake or source observations. New crop
bytes must be reviewed again. Explicit plan revision retains prior FAIL
contracts and re-runs the real plan audit; it does not adopt an old rejection as
PASS. Legacy failed sessions without a durable checkpoint are not claimed to
support that restoration.


## Unified evidence and analysis recovery

PR126 and PR127 compose through one `style-analysis-checkpoints.json` journal.
`replica_style_evidence.py` owns deterministic source/panel/step crop validation
and physical recrop proposals; it does not own workflow state or an independent
model cache. The analysis adapter persists early stages, uses the same hash-bound
request cache for crop reviews, and materializes all proposed crops before
computing review image hashes. A changed crop can never inherit an old pixel
review merely because its filename or stage name is unchanged.

The analysis policy binds the analysis adapter, physical evidence service and
native adapter sources. Old failed analysis contracts remain unchanged; explicit
plan correction records its parent snapshot hash and preserves the original
snapshot in private lineage. Existing style tasks with no durable checkpoint
require one explicit new diagnosis using their retained original material.

Crop review uses a crop-only patch contract. The server owns frozen panel,
source-step, narrative and semantic observations; the model returns only unit
changes, crop proposals and optional mechanisms. Omitted frozen fields are not
treated as rewritten observations. A final review must explicitly cover every
current unit and all eight safety flags. Partial proposals may create real crops
but cannot pass until the new pixels receive a complete review. Old safe flags
never fill missing review fields, and true unsafe verdicts remain failures.

Normal retry admits one narrowly scoped v2-to-v3 policy upgrade for the known
v2 source hash at a failed pre-generation crop review. It requires unchanged
inputs, native Skill source and hash-verified review state, with no jobs or
approved plan. The complete old snapshot, source observations, raw responses and
usage attribution remain preserved. New review requests use the v3 policy;
completed intake is not repeated. Other policy changes require diagnosis.


The compiler's `(path, reference_input_id)` de-duplication can lose a second
source-step binding when one job uses the same reviewed crop for multiple steps
within the same physical panel. The generation workspace adds a real,
byte-identical file alias only for a step that this actual job would otherwise
lose. Each alias preserves the canonical crop, source/bbox, original review and
SHA, and is marked as inherited identical pixels. No new visual PASS is created.
Separate jobs with one step continue to share the canonical crop. Cross-panel
misbinding still fails. Both generation-workflow and manifest copies receive the
same normalized manifest before any generation token is issued; the confirmed
workflow, route plan and source manifest remain unchanged.

## Current exact plan correction

The application recovery layer also admits explicit correction of a rejected
current-v6 plan. Admission checks unchanged inputs and protocol, hash-verified
planning/session/context files, a rejected plan matching its native session,
and no approved execution or image jobs. It retains the original snapshot in
lineage and records the correction authority privately. It does not alter the
v6 model protocol, prompts, original Skill or any archived protocol binding.

The existing native planner changes only failed modules and reuses unchanged
subject-bound PASS reviews. Each explicit correction grants the existing three
planning rounds; a persisted receipt ensures transport retries and process
restarts cannot grant more rounds. Once approved, the normal worker delivers
generated files with zero post-generation AI review. Synthetic HTTP/worker tests
cover temporary-directory deletion, duplicate correction requests, retained
PASS modules, bounded retries and automatic submission/delivery.

## Automatic completion and website records

Website sessions use `auto_start_after_plan=true`. When bounded analysis or
quality correction ends without approval, the application prepares a separate,
hash-bound best-effort execution plan and starts generation automatically.
Original FAIL judgments, snapshots and usage remain intact; this policy creates
no replacement PASS and performs no post-generation AI review. Existing failed
automatic sessions with a valid pre-generation checkpoint recover on their next
normal document read without granting more analysis rounds. Manual API sessions
retain their explicit confirmation/revision behavior.

Best-effort style generation uses product originals and available text
observations, so rejected reference crops are never passed off as safe image
inputs. Source hashes, workspace ownership, delivery bytes and paid invocation
fences still apply. Unknown image outcomes retain their invocation tokens and
cannot silently issue another paid request.

The workspace-scoped creation-record endpoint exposes small session summaries
from persisted records for the same 30-day history window as image jobs. Global
history, task center and navigation merge each session with its current and
previous retry jobs into one entry that opens the original replica session.
Analysis-only sessions appear before an image job exists.

History reads small document headers first. A bounded, database/workspace/version
scoped cache reuses unchanged summaries; PostgreSQL expands each changed private
checkpoint only once with `jsonb_to_record`. Status and update timestamps are
checked on every read, and linked image-job status is always read fresh. The
cache does not bypass workspace authorization or retention filtering.

“新建复刻” clears all local images, facts, links, selections, parameters, URL
session identifiers and draft storage, and creates a fresh request identifier.
Late uploads, analysis responses and polling from the previous draft cannot
restore its contents. “返回修改” retains materials; prior server sessions remain
available from history.
