Approved Huipin experience release, 2026-10-30.

This is a scoped release for the existing haiyu-huipin production stack.
The captured baseline is the running main-image-recovery 32d60f1 image pair,
schema 20261030_0035, with no running generation or replica tasks.
The scripts fail closed if this baseline changes. Do not use them as a general
deployment entry point or against the sister website.

Commit reviewed source first, then run package_release.py locally. Upload the
resulting release-package/experience-20261030/source.tar.gz to a new directory
/srv/huipin-experience-20261030 and extract there. Use a fresh suffixed directory
for each replacement candidate; retain the previous logs. On the approved host run:
python3 release.py build
python3 release.py test
python3 release.py activate
python3 probe_public.py

Builds inherit the existing production images and preserve old frontend assets.
Tests use a disposable PostgreSQL container, including the complete migration
chain and billing, support, replica recovery, membership and payment regressions.
Activation requires matching tested image IDs, idle task queues and unchanged
production baseline. It backs up the database and verifies the dump catalog,
keeps a private copy of the live override, applies additive migration 0036 and
changes only the Huipin application image tags in the current Compose overlay.

Environment values, commands, mounts, resource limits, three queues, 3000 image
slots, protocol hashes and unrelated services are checked. Failure restores the
old image configuration; the additive support table is retained on rollback.
The public probe uses a disposable synthetic identity with SMS mocked, verifies
login, payment catalogs, 5/8/15 tariffs and scoped routes, and cleans up its data.
It does not create payment orders, support requests, fetch products or generate.

Server backups and private container snapshots must remain on the server with
0600 permissions. Only sanitized JSON reports and test summaries may be committed.
