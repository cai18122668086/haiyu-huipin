2026-10-29 consolidated functional release
Baseline: 75b5ac8; deployed fast replica v2 + segmentation v16.
Included: bounded planner output handling, refined plan nesting normalization,
original portrait delivery with verified pixel/aspect limits and actual-size UI.
Excluded: local case-preview pages, user input/output images, private test receipts.
No migration. Preserve payment env/mount, API credentials, worker queues and 3000 slots.

Commit changes first, then run package_release.py. Upload source.tar.gz to
/srv/huipin-consolidated-20261029 and extract there. Existing provider credential
is retained server-side only as provider.private.env (0600). Run sequentially:
python3 release_consolidated.py build
python3 release_consolidated.py test
python3 release_consolidated.py activate

The inherited audited release_base.py verifies sources and image IDs, runs tests
against disposable PostgreSQL, requires no active work, snapshots config and DB,
preserves protected resources, and rolls back on failed activation checks.
No paid generation/crawler request is made by this release flow.
