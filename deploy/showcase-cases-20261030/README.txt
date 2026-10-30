Approved frontend-only showcase release, 2026-10-30.

The owner reviewed the independent 18958 preview and approved publication.
Keep the four main entry cards; secondary outcomes use equal compact square
media and equal text panels with the existing source/reference/result reveal.
The public example data omits private task IDs, history URLs and review notes.
No development auth settings, local preview controls or review route are shipped.

Package only the scoped committed frontend files and approved referenced images.
Build FROM the captured current production frontend. Preserve its previous
hashed client assets. Verify source hashes and start a private loopback candidate.
Activation changes only services.frontend.image in the existing effective stack,
with protected configuration, environments, commands, resources and unrelated
container IDs checked. API, workers, dispatcher and database remain running.
Rollback restores the private previous overlay and the previous frontend image.

Server rollback snapshots contain private runtime data and must stay on the
server with mode 0600. Only sanitized build/test/activation results are publishable.
