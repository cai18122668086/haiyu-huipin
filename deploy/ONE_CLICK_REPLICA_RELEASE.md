# One-click replica scoped production release

Product PR #79 targets an independent release branch based on production commit 639735b. The other task's checkout and branch are not deployment inputs. The release commit has one product parent and changes only `deploy/**`.

## Scope

`prepare_one_click_replica_release.py` reads each live role's exact Compose configuration on the server, preserving external authentication, quotas, storage, networks, hardening and secrets. It writes a private 0600 Compose file containing exactly `replica-api`, `replica-worker`, `dispatcher`, and `frontend`. Only run `up --no-deps` for these roles. Do not run the old combined Compose file or recreate detail services.

The two replica services use Tengli primary / Wanzhuan backup for analysis and Artflow primary / Tengli backup for images. Image fallback is explicitly enabled only on these isolated roles; planner backup credentials never enter image routes. Primary generation uses the website's fixed gpt-image-2.5-flare model, with Tengli's configured image model on fallback, and queue `one_click_replica`. Only replica-api receives the Just One token. Credentials remain on the VM. Browser profiles are not mounted. No new host ports are published.

Backend candidate is built offline from the exact current API image plus reviewed app/migration sources. Dispatcher candidate copies only the additive outbox routing change onto the exact current dispatcher image. Frontend candidate keeps previous hashed `_polaris-static` files after rebuilding, so existing open pages can still load their assets. Build with `VITE_LOCAL_DEV_AUTH=false` and retain last known good images.

## Backup and migration

The user requires concurrent detail work to remain running. For this additive table release, use an online database snapshot followed by a logical copy of immutable S3 objects. `replica_backup_restore.py` restores to a new internal Docker network and new PostgreSQL/MinIO volumes; it verifies all ready, live database asset checksums against restored objects and rehearses migration 0027. Existing services and production volumes are never restored or stopped. The only stop targets are the two explicitly named restore-drill containers, with IDs checked against production.

Run `replica_restored_mock_smoke.py` in that isolated network using the private restore environment to check API readiness, upload, and one completed mock image job. Preserve the backup manifest, SHA-256 checksums and restore evidence on the server. Then run `python -m alembic upgrade head` using the replica service's production configuration, with a bounded PostgreSQL lock timeout. The migration only creates `replica_documents`, its foreign key and index. Application rollback preserves this table and all imported assets.

## Cutover

Before any mutation compare the live container IDs/images and active Caddyfile SHA-256 with the prepared baseline; stop the release if they changed. Start replica-api/replica-worker and require health checks plus destination-specific worker ping. Replace dispatcher with its minimal patch, preserving the other queues.

Insert only the following handler inside the existing `handle @api`, before the current job handler:

```caddyfile
@one_click_replica path /api/v1/workspaces/*/replica/*
handle @one_click_replica {
    reverse_proxy replica-api:8000
}
```

Patch the live Caddyfile in place after a timestamped backup. Keep the existing detail routing, API transit domain, root-domain redirect and payment-domain blocks byte-for-byte. Do not replace the live file with the repository's shorter template. Validate with the existing Caddy binary before applying. This host's Caddy is v2.10.2 with `admin off`, so SIGUSR1 reload from newer Caddy versions is unavailable; use the existing supervised restart method only at cutover, with no active Polaris generation calls. The API and worker processes continue running.

Switch frontend last. Verify the public navigation, custom dropdown, link fetch, image selection/import, analysis, confirmation, dedicated queue generation and direct image download under the existing signed-in owner session. Check detail container IDs, images, health, and active task status again. Rollback switches only this release's frontend/dispatcher images and Caddy route, leaving database data and detail services intact.

## Verified before cutover

- 150 frontend regression tests; build, TypeScript and ESLint.
- 184 merged-baseline API/reference/detail regression tests, followed by 98 tests after the real-provider context correction.
- Real Just One imports for Taobao/Tmall, JD and 1688. Correct 1688 sample: 5 gallery, 17 detail, 32 SKU images.
- Real Tengli analysis and image generation, direct attachment download. Final analysis uses requested Chinese copy and square canvas.
- Paired backup restore: 860 objects, 851 live DB assets checksum verified; 0026 to 0027 rehearsal; isolated API and mock generation passed.

Pinduoduo link collection is not included; users can upload its reference images manually. Current account prices are documented in `docs/one-click-replica-2026-10-22.md`.


Production verification on 2026-10-22:
- Product PRs #79, #80, #81, #82 and #83 were squash-merged into the independent release branch. The main and detail-work branches remain untouched.
- Match all nested replica routes using `path_regexp one_click_replica ^/api/v1/workspaces/[^/]+/replica/`. A mixed-wildcard path matcher covers only one suffix segment in the installed Caddy and must not be used here. Preserve the maintained API/payment domain blocks in the active file.
- Validate through `docker exec -i ... caddy validate --config - --adapter caddyfile`; the proxy root filesystem remains read-only.
- Live external-auth upload, Tengli analysis, confirmation, dedicated Celery queue, generation and page refresh restoration passed. Production job: `job_daf851fa862e40268b3216cd96ca4c5c`.
- Just One has been recharged. Production link retrieval now succeeds: the 1688 sample returns 5 main, 17 raw description and 32 SKU images. Blank or unreadable previews are filtered and checked before import. The legacy browser crawler is not used as an automatic fallback.
- Desktop navigation uses single-line labels; below 1400px the existing complete navigation menu is used.

Navigation sizing is applied at the end of app/focus-inspired.css, after the active theme rules; long account labels are bounded with ellipsis.
