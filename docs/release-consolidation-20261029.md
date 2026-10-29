# 2026-10-29 release consolidation

Reviewed chats: `同步账户管理与会员制度`, `接入一键复刻爬虫`,
`提高网站并发上限`, `排查一键复刻生成问题`, `审计套图 Skill 生成耗时`,
`用真实素材展示平台功能效果`, `排查真实素材展示功能失败`.

The first five functional changes were already integrated on main (75b5ac8) and
running in the fast replica v2 images. This release adds the remaining functional
patches from the real-material investigation:

- Reuse bounded DeepSeek planner settings for refined detail and Amazon suites;
  preserve full schemas and stop explicitly on truncated output.
- Normalize complete authored refined screen plans without fabricating content.
- Deliver full original portrait files satisfying aspect and native pixel limits;
  retain actual/requested dimensions and a hash receipt through the DB boundary.
- Display actual output dimensions and describe refined delivery as native 3:4.

Compatibility check caught a content-addressed protocol dependency: the new
portrait helper lives outside replica_canvas.py, preserving v16 and historical
v15 fingerprints and their recovery paths. Fast replica's 20-way concurrency,
standard/fast selector, collection/payment configuration and three worker queues
remain part of this branch's base.

Local case-review UI and real user material remain local because the original
homepage image replacement was awaiting visual approval. They are excluded from
source and deployment packages. No user material, private env, database or test
credentials are included.

Prior live evidence: refined detail's six 1086x1448 originals pass normal delivery
and ZIP byte checks after this fix. Amazon planning and 20 images succeeded; one
upstream image remained unreadable. This release does not claim that upstream
file was repaired or that a complete Amazon suite was delivered.

Deployment evidence is recorded in verification/consolidated-20261029 after the
candidate build, disposable PostgreSQL regression and guarded production cutover.
No new migration or paid generation request is required by this release.
