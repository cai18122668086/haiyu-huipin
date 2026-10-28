# Generation and audit integrity (v4.2)

## Signed generation transaction

Use `scripts/generation_adapter.py` as the only generation handoff. `prepare` emits a request with a session ledger entry and `request_fingerprint`; `commit` immediately binds the returned raster through `record-generation`. A local provider CLI can use `run` and must return UTF-8 JSON containing `{"image":"<absolute-path>"}`. The artflow provider is a local CLI: use `prepare`, run `python scripts/artflow_provider.py --request <request.json> --out-dir <dir>` with the unchanged request, then call `commit` immediately with the returned `image` path. `artflow_generate.py` sends the request's `output_surface` as the exact generation canvas, so the blank `format_canvas` is not forwarded as an image reference.

Never hand-author a request fingerprint or edit a request after it is issued. A changed request cannot be recovered through batch import.

## Controlled candidate recovery

`record-generation-batch` accepts several candidate rasters only for one already-issued active request. The import manifest binds the request path and fingerprint, every candidate token, ordered module IDs, pair ID and image SHA-256. Exactly one candidate enters the normal normalization, slicing and pixel-audit path; unselected candidates remain recovery evidence and do not become attempts.

This command cannot import unrequested later pairs, reorder modules, accept pixels, or skip `audit-pair`.

## Pixel judgment binding

Every module, pair and long-page judgment includes `artifact_binding` with the audited image SHA-256, auditor identity, ISO-8601 audit time, captured OCR text and/or a structured visual-inspection output. The harness recomputes the actual file hash before accepting the judgment.

## Explicit automatic delivery

Automatic final delivery is permitted only when plan authorization explicitly uses `--delivery-mode AUTO_FINALIZE_AUTHORIZED`. After all pairs are audited, the session enters `AUTO_FINALIZE_AUTHORIZED`; only `auto-finalize` can produce the manifest. Ordinary approval continues to require a separate final review and `finalize --approval-text`.
