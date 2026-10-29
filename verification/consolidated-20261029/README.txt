Consolidated release validation, 2026-10-29
Source: 3f01230310c78e76f260435743801e7af8c1f4f6 (functional patch ce56815).
Built on running fast replica v2 / segmentation v16, not the older image baseline.

chat-audit.json: related chat status and inclusion/exclusion decisions.
build.json: image IDs and checked sources (364 backend, 570 frontend).
postgres-regression.log / test.json: final complete candidate run, 752 passed,
1 preexisting capacity expectation deselected; isolated disposable PostgreSQL,
no production database or paid provider request.
postgres-regression-first/second.log: retained full failed runs; each 751 passed
and the same 0.4-second heartbeat storage assertion failed. The candidate's
reliability module also passed 37/37 in isolation before the test repair.
heartbeat-negative-control.json: disabling keeper renewal causes both revised
heartbeat cases to fail as expected. Runtime lease settings were not changed.
real-native-originals.json: six prior real originals rechecked with the final
portrait helper and compared byte-for-byte with the downloadable ZIP.

activation.json: actual cutover, image tags, preserved settings and server-only
backup location. runtime.json: healthy services, original v16/v15 fingerprints,
fast 20 concurrency, shared 3000 capacity, worker queues and WeChat query mode.
public-probe.json: login/session, payment catalog, 12 collection sources, customer
permissions and synthetic-account cleanup. No SMS, crawler or generation call.
Private credentials, configuration snapshots and database backups remain on the
server only. User images and complete private provider receipts are not committed.
