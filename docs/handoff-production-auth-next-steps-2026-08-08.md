# Polaris Commerce Studio 生产认证下一步规划与交接

> 交接日期：2026 年 8 月 8 日（星期六）
> 当前仓库：`D:\Projects\polaris-commerce-studio-frontend-optimization`
> 当前分支：`codex/login-system`
> 当前基线 HEAD：`ac9bfb8192f190b79d94bf1a28c7d9ef47c64163`
> 文档定位：供下一轮审核、提交、邮件/DNS 准备和生产切换任务直接接手
> 授权状态：**本文档只做规划和交接，不授权 commit、push、PR、SSH 写操作或生产切换**

---

## 1. 一句话结论

下一步**不是立即部署生产**，而是先完成：

1. 冻结当前登录系统和生产认证基础设施改动；
2. 审查完整 diff、依赖、迁移、Owner 脚本、Secret 和能力边界；
3. 在用户明确授权后提交并推送 `codex/login-system`，记录可审核的精确 40 位 SHA；
4. 再选择真实 SMTP 服务商、配置 SPF/DKIM/DMARC；
5. 完成 PostgreSQL + MinIO 成对备份和隔离恢复演练；
6. 取得维护窗口批准后，才允许从精确审核 SHA 切换 production；
7. 最后进行真实注册、验证、Session refresh、退出、密码重置和历史 Workspace Owner 分配。

在工作树未审核、未提交、未推送，SMTP/DNS 和备份 Gate 未完成前，**不得把当前本地脏工作树部署到服务器**。

---

## 2. 本轮已经完成的内容

### 2.1 应用层认证

已经完成并验证：

- SuperTokens 自托管邮箱密码注册和登录；
- 公开注册；
- 强制邮箱验证；
- 忘记密码和密码重置；
- Secure Cookie Session；
- Session refresh；
- External 模式忽略客户端 `X-User-Id`；
- 未登录返回 `401`；
- 邮箱未验证返回 `403 EMAIL_NOT_VERIFIED`；
- 认证服务不可用返回 `503 AUTHENTICATION_UNAVAILABLE`；
- disabled/suspended 用户拒绝访问；
- SuperTokens 用户首次访问时建立本地 JIT 用户映射；
- 邮箱规范化和邮箱变更同步；
- `/api/v1/me` 返回真实账户信息；
- `/account` 不依赖 Workspace。

### 2.2 Workspace onboarding

已经完成：

- 新用户第一次进入需要 Workspace 的业务 API 时自动创建个人 Workspace；
- 新用户自动成为个人 Workspace Owner；
- 自动创建 Workspace Limit；
- 自动创建 Credit Account；
- 初始积分固定为 `0`；
- 创建过程支持幂等和并发冲突恢复；
- 提供显式、幂等的历史 Workspace Owner 分配脚本。

Owner 脚本：

```text
backend/scripts/assign_workspace_owner.py
```

该脚本只修改一个明确 Workspace 的 `workspace_members`，不修改历史素材、任务、creator 或其他业务外键，也不会隐式操作全部 Workspace。

### 2.3 前端认证

已经加入：

- `/auth/login`；
- `/auth/register`；
- `/auth/verify-email`；
- `/auth/forgot-password`；
- `/auth/reset-password`；
- `/account`；
- `AuthBoundary`；
- 登录后安全返回原业务页面；
- 开放重定向防护和 `/auth/*` 循环防护；
- API 请求统一携带 Cookie；
- Session 最终失效后清理 Query Cache 并跳转登录；
- 顶部导航显示真实账户信息；
- 不在 localStorage/sessionStorage 保存 Token。

### 2.4 生产认证基础设施

已经新增或完成：

- `deploy/compose.production.yml`；
- `deploy/Caddyfile.production`；
- `deploy/.env.production.example`；
- `deploy/generate_production_env.py`；
- `deploy/production_preflight.py`；
- `deploy/tests/test_generate_production_env.py`；
- `deploy/tests/test_production_preflight.py`；
- `docs/production-auth-deployment-2026-08-08.md`；
- 对 `deploy/preflight.py`、`deploy/README.md`、`deploy/backend.Dockerfile` 的必要更新。

Backend 镜像只额外打包：

```text
/app/scripts/assign_workspace_owner.py
```

Production preflight 会阻止：

- Owner 脚本未进入镜像；
- 将全部 `backend/scripts` 打入镜像；
- Secret、镜像引用、Provider 配置或生产拓扑不符合既定约束；
- Caddy、Compose 或共享卷配置偏离生产设计。

---

## 3. 当前 Git 状态

截至 2026 年 8 月 8 日复核：

```text
分支：codex/login-system
HEAD：ac9bfb8192f190b79d94bf1a28c7d9ef47c64163
upstream：尚未配置
已暂存：0
modified：24 个顶层状态项
untracked：21 个顶层状态项（展开后包含多个新文件）
```

当前没有：

- stage；
- commit；
- push；
- PR；
- 生产部署。

不得：

- reset、clean、checkout 覆盖或丢弃当前工作树；
- 把当前未提交文件直接复制到生产服务器作为 release；
- 在没有明确授权时自行 stage、commit 或 push；
- 将真实 Secret 写入仓库、文档、命令历史或聊天记录。

---

## 4. 已确认的关键设计决定

### 4.1 SuperTokens Core

固定生产镜像：

```text
supertokens/supertokens-postgresql:12.0.10@sha256:4516ec7c00b8fb2a773012694d8ebf03b54799d59cfc974160590f16d235ce72
```

已完成 PostgreSQL PoC，确认 Core `v12.0.10` 可启动、`/hello` 正常、可使用 read-only root filesystem 和 drop all capabilities，并会建立所需数据库表。

Core API Key 只允许使用：

```text
A-Z a-z 0-9 = -
```

生产 env 生成器使用 `secrets.token_hex(48)`，不会生成带 `_` 的不合法 key。

### 4.2 SMTP

应用固定发件人：

```text
Polaris Commerce Studio <no-reply@mail.tengliapi.xyz>
```

代码采用标准 SMTP，不绑定具体服务商。当前尚未选择真实服务商，也没有真实 SMTP 凭据。

SPF 和 DKIM 必须严格使用服务商提供的准确值，不得猜测或杜撰。

初始 DMARC 可采用监控策略：

```text
_dmarc.tengliapi.xyz TXT "v=DMARC1; p=none; adkim=r; aspf=r; pct=100"
```

在真实邮件长期通过 SPF/DKIM alignment 后，再单独审批升级到 `quarantine` 或 `reject`。

### 4.3 域名和 HTTPS

2026 年 8 月 8 日审计快照：

```text
www.tengliapi.xyz -> 43.129.198.147
tengliapi.xyz     -> 104.244.95.188
```

因此 production Caddy 只声明：

```text
www.tengliapi.xyz
```

不得抢占 apex `tengliapi.xyz`。正式切换前必须重新查询 DNS，因为审计快照可能变化。

### 4.4 共享数据卷

Production 显式复用 internal 的卷：

```text
polaris-internal-staging_postgres_data
polaris-internal-staging_redis_data
polaris-internal-staging_minio_data
```

因此 internal 与 production **绝不能同时运行**。

切换顺序必须是：

1. 阻断新写入并处理队列/outbox；
2. PostgreSQL + MinIO 成对备份；
3. 在全新临时项目和全新卷中完成隔离恢复演练；
4. 停止 internal，但不删除卷；
5. 确认三个共享卷没有任何容器使用；
6. 仅从审核过的精确 SHA 和已构建镜像启动 production。

永远禁止：

```text
docker compose down -v
docker volume prune
docker system prune --volumes
```

---

## 5. 已通过的验证

本轮已有通过记录：

- `python deploy/preflight.py --mode source`；
- deploy tests：67 passed；
- production preflight；
- production Compose `config --quiet`；
- Caddy `Valid configuration`；
- 完整 backend test suite；
- Ruff；
- Mypy；
- 前端认证策略测试：3 passed；
- TypeScript；
- ESLint；
- production frontend build；
- OpenAPI 导出稳定；
- TypeScript OpenAPI 类型生成稳定；
- `git diff --check`；
- Secret 扫描；
- 编码损坏扫描；
- `backend/skill_sources/**` 无改动。

本机 Node 为：

```text
v22.11.0
```

构建虽已成功，但仓库 `package.json` 要求：

```text
>=22.13.0
```

正式审核构建和生产构建必须使用 Node `22.13.0` 或更高版本，不应降低 engine 要求来适配旧 Node。

上述结果是当前工作树的历史验证记录。任何实质代码修改、依赖变化、rebase 或冲突解决后，都必须重新运行对应 Gate。

---

## 6. 当前真实阻断项

以下项目全部解除前，不得生产切换：

1. 当前完整 diff 尚未完成人工 review；
2. 工作树尚未 commit 和 push；
3. 分支尚无 upstream，也没有 PR/review 记录；
4. 尚未选择真实 SMTP 服务商；
5. 尚未通过安全渠道取得 SMTP host、port、username、credential 和 TLS 模式；
6. 尚未取得并发布服务商准确 SPF；
7. 尚未取得并发布服务商准确 DKIM selector/target；
8. DMARC 尚未发布；
9. 尚未完成 PostgreSQL + MinIO 成对备份；
10. 尚未在隔离项目和新卷中完成恢复演练；
11. 尚未批准 stop-and-replace 维护窗口；
12. `1474953130@qq.com` 尚未在真实生产环境注册和验证；
13. 尚未完成公网 Session refresh、退出和密码重置冒烟；
14. 尚未确认真实历史 Workspace ID；
15. 尚未实际执行历史 Workspace Owner 分配。

---

# 下一阶段执行计划

## 7. Phase 0：冻结、完整审核与待提交清单

### 7.1 目标

把当前脏工作树变成“可以安全提交、可以被别人复核”的确定变更集，而不是继续扩大范围。

### 7.2 输入

- 当前 `codex/login-system` 工作树；
- 本文档；
- `docs/production-auth-deployment-2026-08-08.md`；
- `deploy/README.md` 的 production auth 和 paired-backup 章节。

### 7.3 动作

1. 冻结需求，不再加入支付、套餐、计费或新业务能力；
2. 审查 `git diff` 和所有 untracked 文件；
3. 确认认证路由、Cookie、Session refresh、邮箱验证和 JIT onboarding 的安全边界；
4. 确认 `supertokens-web-js` 已进入 `package.json` 和 lockfile；
5. 确认 `openapi-fetch` 每次请求使用初始化后的 `globalThis.fetch`；
6. 确认 OpenAPI 与生成类型只包含预期认证变化；
7. 审查迁移/模型影响，确认不存在未经说明的数据破坏动作；
8. 审查 `assign_workspace_owner.py`，确认必须显式提供 email 和 Workspace ID；
9. 审查 Dockerfile，确认只加入 Owner 脚本，没有打包全部运维脚本；
10. 扫描真实 Secret、私钥、env 值、SMTP credential、Cookie 或 Token；
11. 确认 Node 审核环境升级到 `>=22.13.0`；
12. 形成精确待提交文件清单。

### 7.4 建议复核命令

```powershell
cd D:\Projects\polaris-commerce-studio-frontend-optimization

git branch --show-current
git rev-parse HEAD
git status --short
git diff --check
git diff --name-only -- backend/skill_sources
git diff --stat
git diff
git ls-files --others --exclude-standard
```

必要测试：

```powershell
python deploy/preflight.py --mode source
backend\.venv\Scripts\python.exe -m pytest deploy/tests

Push-Location backend
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe app scripts tests
Pop-Location

npm run typecheck
npm run lint
npm run build
node --experimental-strip-types --test tests\auth-route-policy.test.mjs
npm run api:export
npm run api:generate
# 审查 docs/openapi-v1.json 和 lib/api/generated/openapi.d.ts 只发生预期变化；
# 在预期生成文件已暂存后，再运行 npm run api:check 验证无额外工作树漂移。
```

如果本地虚拟环境命令入口不同，应使用仓库已验证的等价命令，但不得跳过测试类别。

### 7.5 建议提交拆分

在用户授权 stage/commit 后，优先按逻辑拆分：

1. `feat: integrate production-ready authentication`
   - 后端认证、JIT onboarding、Owner 脚本；
   - 前端认证页面、AuthBoundary、账户页；
   - 依赖、测试、OpenAPI 和生成类型。
2. `ops: add production authentication deployment profile`
   - production Compose/Caddy；
   - env 生成器、production preflight、Dockerfile 和 deploy tests；
   - deploy runbook。
3. `docs: add authentication handoff and next-step plan`
   - 交接和规划文档。

如果拆分会导致中间提交无法构建或测试失败，可合并为较少的原子提交；不得为了形式拆分出不可运行的中间状态。

### 7.6 Gate

Phase 0 只有在以下全部满足时通过：

- `git diff --check` 无输出；
- `git diff --name-only -- backend/skill_sources` 无输出；
- 无支付、Provider、生图或 Amazon 能力改动；
- 无真实 Secret、私钥、Token、Cookie 或 populated env；
- 所有必要测试通过；
- OpenAPI 和生成类型稳定；
- Owner 脚本和 production Dockerfile 边界正确；
- 待提交文件清单已人工确认。

### 7.7 回滚点

本阶段不执行生产操作。若审核发现问题，只修复对应问题并重跑 Gate；不得用 `reset --hard` 或 `clean -fd` 丢弃当前工作树。

---

## 8. Phase 1：经授权提交、推送和代码审查

### 8.1 前置授权

必须由用户明确授权：

- stage 哪些文件；
- 是否按建议拆分 commit；
- 是否 push；
- 是否创建 PR。

### 8.2 动作

审核通过后：

1. 只 stage 已确认文件；
2. 运行 cached diff 和 Secret 检查；
3. 创建原子 commit；
4. 记录最终 40 位 commit SHA；
5. 推送并设置 upstream；
6. 最好创建 PR，由另一个审查者复核；
7. 部署只允许使用经批准远端引用可达的精确 SHA，优先使用已合并到 `main` 的 SHA。

参考命令，**以下命令不构成当前授权**：

```powershell
git add <APPROVED_FILES>
git diff --cached --check
git diff --cached --stat
git diff --cached
git commit -m "<APPROVED_COMMIT_MESSAGE>"
git push -u origin codex/login-system
git rev-parse HEAD
```

### 8.3 Gate

- 本地工作树处于预期状态；
- commit 可从远端分支读取；
- 记录精确 40 位 SHA；
- CI/必要验证通过；
- review 无未解决阻断问题；
- 发布 SHA 与构建 SHA 完全一致。

### 8.4 回滚点

未部署前，问题应通过新修复 commit 处理。不要 force-push 已经进入 review 的历史；不要修改 `main` 历史。

---

## 9. Phase 2：选择 SMTP 服务商并完成 DNS

### 9.1 用户必须提供或决定

- 事务邮件服务商；
- SMTP host；
- SMTP port；
- username；
- password/API credential 的安全交付渠道；
- implicit TLS 或 STARTTLS；
- 发信域名验证要求；
- 服务商给出的准确 SPF；
- DKIM selector 和准确 TXT/CNAME target；
- 自定义 MAIL FROM/bounce domain（如服务商要求）；
- DNS 管理权限或由谁执行 DNS 变更。

不得把真实 credential 粘贴进 Git、普通文档或公开聊天。

### 9.2 DNS 动作

1. 查询现有 TXT，避免重复 SPF；
2. 按服务商准确值发布或合并 SPF；
3. 按服务商准确 selector/target 发布 DKIM；
4. 发布 DMARC `p=none`；
5. 用至少两个公共解析器验证传播；
6. 在服务商控制台确认域名和发信身份通过。

### 9.3 Gate

- hostname 上只有一条有效 SPF；
- DKIM 与服务商显示完全一致；
- DMARC 可被公共 DNS 解析；
- 服务商确认发件身份可用；
- 发件地址保持 `no-reply@mail.tengliapi.xyz`；
- 没有把 SMTP Secret 写入仓库或命令输出。

### 9.4 回滚点

DNS 值错误时只回滚本阶段新增/修改的准确记录。不要删除不属于本项目的现有记录，也不要猜测替代值。

---

## 10. Phase 3：构建精确生产 release

### 10.1 前置条件

- Phase 1 和 Phase 2 已通过；
- 已批准精确 SHA；
- 服务器只读复核仍符合部署条件；
- 构建环境使用 Node `>=22.13.0`；
- 服务器 release checkout 工作树干净。

### 10.2 动作

在服务器检出审核 SHA，并确认：

```bash
cd <SERVER_RELEASE_CHECKOUT>
git status --short
git rev-parse HEAD
git branch --show-current
```

`git status --short` 必须无输出，HEAD 必须等于审核过的 40 位 SHA。

交互生成 production env：

```bash
sudo python3 deploy/generate_production_env.py \
  --base-env /etc/polaris/polaris.env \
  --output /etc/polaris/polaris.production.env \
  --release-sha <REVIEWED_40_CHARACTER_SHA>
```

只检查文件所有权和权限，不读取 Secret 内容：

```bash
sudo chown root:root /etc/polaris/polaris.production.env
sudo chmod 0600 /etc/polaris/polaris.production.env
sudo stat -c '%U:%G %a %n' /etc/polaris/polaris.production.env
```

运行 fail-closed preflight：

```bash
sudo python3 deploy/production_preflight.py \
  --env-file /etc/polaris/polaris.production.env \
  --base-env /etc/polaris/polaris.env

sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  config --quiet
```

构建不可变应用镜像，记录 image ID/digest、SBOM 和扫描结果：

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  build --pull=false api frontend
```

使用同一 Caddy 镜像校验实际挂载文件：

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  run --rm --no-deps --entrypoint caddy caddy \
  validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

禁止对 populated env 使用 `cat`、`grep`、`env`、`set -x`，也不要输出完整 `docker compose config`。

### 10.3 Gate

- release checkout 干净；
- HEAD 等于批准 SHA；
- env 为 `root:root 0600`；
- preflight PASS；
- Compose `config --quiet` PASS；
- Caddy validate PASS；
- 所有镜像不可变且与 SHA 对应；
- image ID/digest、SBOM 和扫描证据已记录；
- 没有 Secret 泄露。

### 10.4 回滚点

本阶段尚未切换共享卷。任何失败都应停止，不启动 production；修复代码必须回到 Git/PR 流程生成新 SHA，禁止在服务器直接热改 release 文件。

---

## 11. Phase 4：成对备份、恢复演练和维护窗口切换

### 11.1 前置条件

- 已批准维护窗口；
- 已准备回滚负责人和判定标准；
- 精确 release 和镜像已通过 Phase 3；
- 备份目标和独立存储已批准；
- 恢复演练使用新 Compose project 和新卷。

### 11.2 成对备份

必须把 PostgreSQL 和 MinIO 视为同一时间点的数据对：

- 阻断入口和所有 writer；
- 处理或记录 pending queue/outbox；
- PostgreSQL custom-format dump；
- writer 停止后的完整 MinIO 数据快照；
- 记录 UTC 时间、release SHA、schema revision、image ID；
- 记录行数、对象数、字节数和 SHA-256；
- 将两个 artifact 复制到独立批准存储。

只备份数据库或只备份对象都不合格。

### 11.3 隔离恢复演练

在全新临时项目和全新卷中：

1. 恢复 PostgreSQL dump；
2. 恢复 MinIO snapshot；
3. 验证 checksum；
4. 验证行数和对象数；
5. 验证 schema revision；
6. 验证 API readiness；
7. 执行一个不消耗真实 Provider 的 mock job。

禁止对活动数据库执行 `pg_restore --clean`，禁止复用活动项目名和活动卷。

### 11.4 切换

停止 internal，但不删除卷：

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.env \
  -f deploy/compose.internal.yml \
  stop
```

确认共享卷无人使用：

```bash
for volume in \
  polaris-internal-staging_postgres_data \
  polaris-internal-staging_redis_data \
  polaris-internal-staging_minio_data
do
  sudo docker ps --filter volume="$volume" --format '{{.ID}} {{.Names}}'
done
```

三个查询都必须无输出。

启动 production：

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  up -d --no-build --pull never

sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  ps
```

### 11.5 Gate

- 成对备份完成；
- 隔离恢复演练成功；
- internal 已停止；
- 三个共享卷无使用者；
- migration 成功；
- 所有长运行服务 healthy；
- 无 restart loop；
- 只有 Caddy 发布公网 80/443；
- PostgreSQL、Redis、MinIO、API、frontend、SuperTokens 无公网监听。

### 11.6 回滚点

若 migration、健康检查或监听边界失败：

1. 停止 production 入口和 writer；
2. 保留日志和脱敏证据；
3. 判断 schema 是否向后兼容；
4. 兼容时，使用保留的精确旧镜像/env 恢复 internal；
5. 不兼容时，只按批准恢复方案使用成对备份；
6. 禁止盲目 `alembic downgrade`；
7. 始终确保只有一个项目连接共享卷。

---

## 12. Phase 5：真实生产认证冒烟

使用普通浏览器和 DevTools。密码不得出现在终端、截图、日志或交接文档中。

测试账号：

```text
1474953130@qq.com
```

### 12.1 基础设施验收

- `https://www.tengliapi.xyz` 证书有效；
- HTTP 自动跳转 HTTPS；
- `/health/live` 和 `/health/ready` 正常；
- SuperTokens 3567 只在 Docker 私网可达；
- 非 Caddy 服务没有公网监听。

### 12.2 认证流程验收

1. 打开 `/auth/register` 注册；
2. UI 要求邮箱验证；
3. 邮件 From 为 `no-reply@mail.tengliapi.xyz`；
4. 验证链接 host 为 `https://www.tengliapi.xyz`；
5. 完成验证并进入 `/account`；
6. 确认显示真实邮箱；
7. 首次进入需要 Workspace 的页面/API；
8. 确认个人 Workspace 自动创建；
9. 确认用户为 Owner；
10. 确认 Workspace Limit 和 Credit Account 已创建；
11. 确认初始积分为 `0`；
12. 检查 Cookie 为 `Secure`、`HttpOnly`，预期 `SameSite=Lax`；
13. 确认 localStorage/sessionStorage 无 Token；
14. 等待或触发 access token 过期，确认 Session 自动 refresh；
15. 退出后 protected API 返回 `401`；
16. 验证未完成的账号访问受保护业务时返回 `403 EMAIL_NOT_VERIFIED`；
17. 发起密码重置；
18. 确认重置链接 host/HTTPS 正确；
19. 确认旧密码失败、新密码成功；
20. 再完成一次退出和登录。

### 12.3 邮件身份验收

保留不含 Secret 的证据：

- 投递时间；
- From 和 recipient；
- 可安全记录的 provider event ID；
- SPF pass/fail；
- DKIM pass/fail；
- DMARC pass/fail；
- 验证和重置链接 host。

完整邮件头可能包含敏感信息，保存前必须脱敏。

### 12.4 Gate

注册、验证、登录、refresh、退出、密码重置、Cookie 属性、无前端 Token 存储、个人 Workspace onboarding 和邮件身份验证全部通过。

### 12.5 回滚点

如果认证主链路失败，不执行历史 Owner 分配。根据故障位置停止 production 入口/认证流量，按 Phase 4 回滚标准处理。

---

## 13. Phase 6：显式分配历史 Workspace Owner

### 13.1 前置条件

- `1474953130@qq.com` 已完成真实注册和邮箱验证；
- 本地 JIT user 已建立；
- Phase 5 已通过；
- 成对备份仍有效；
- 已列出候选 Workspace；
- 用户明确确认一个真实 Workspace ID。

禁止根据名称、创建时间或猜测自动选择 Workspace。

### 13.2 列出候选 Workspace

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  exec -T postgres \
  psql -U polaris -d polaris -c \
  "SELECT id, name, status, created_at FROM workspaces ORDER BY created_at, id;"
```

### 13.3 用户批准后执行

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  exec -T api \
  python scripts/assign_workspace_owner.py \
  --email '1474953130@qq.com' \
  --workspace-id '<APPROVED_HISTORICAL_WORKSPACE_ID>'
```

脚本预期幂等结果：

```text
created
restored
unchanged
```

### 13.4 验证

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  exec -T postgres \
  psql -U polaris -d polaris -c \
  "SELECT wm.workspace_id, u.email_normalized, wm.role, wm.status FROM workspace_members wm JOIN users u ON u.id = wm.user_id WHERE u.email_normalized = '1474953130@qq.com' ORDER BY wm.workspace_id;"
```

### 13.5 Gate

- 只变更批准 Workspace 的一条 membership；
- role 为 Owner；
- membership 为预期活动状态；
- 历史素材、任务、creator 和其他外键未修改；
- 保存命令时间、批准 Workspace ID 和非 Secret 结果证据。

### 13.6 回滚点

误分配时必须根据变更前证据，对该 membership 做单独、显式、经审核的恢复。禁止通过删除用户、Workspace、素材或任务回滚。

---

## 14. Phase 7：观察期和最终验收

建议至少覆盖一个明确批准的观察窗口，检查：

- Caddy、frontend、API、worker、dispatcher、SuperTokens 日志；
- 容器 restart count；
- HTTP 4xx/5xx；
- 注册、验证和重置邮件失败率；
- 队列积压和 outbox；
- CPU、RAM、磁盘和 inode；
- TLS 证书签发和续期状态；
- PostgreSQL 和 MinIO 备份；
- Session refresh 和登录失败异常；
- Provider 行为和费用没有漂移；
- 历史 Workspace 数据可见性正常。

最终验收记录至少包含：

- 审核 SHA 和远端引用；
- application image ID/digest；
- infrastructure image digest；
- SBOM/扫描证据；
- production preflight；
- Caddy validate；
- 成对备份 manifest；
- 隔离恢复演练结果；
- SPF/DKIM/DMARC 结果；
- TLS 和监听端口；
- 注册、验证、登录、refresh、退出、密码重置结果；
- 批准的历史 Workspace ID；
- Owner 分配结果；
- 观察窗口健康结果；
- 是否触发回滚及最终状态。

---

## 15. 明确禁止范围

下一阶段仍然不得：

- 接入支付；
- 修改套餐、计费或积分商业规则；
- 修改 `backend/skill_sources/**`；
- 修改 Provider 行为或配置语义；
- 修改生图能力；
- 修改 Amazon 协议能力；
- 让 production 与 internal 同时连接共享卷；
- 猜测历史 Workspace ID；
- 在真实用户注册前执行 Owner 分配；
- 泄露 Secret；
- 部署未提交、未推送、未审核的工作树；
- 在服务器 release checkout 中直接热修代码；
- 使用破坏卷的 Docker 命令。

---

## 16. 下一阶段需要用户提供或明确批准的内容

### 在 commit/push 前

- 是否授权 stage；
- 是否授权 commit；
- commit 拆分方案；
- 是否授权 push；
- 是否创建 PR 和由谁 review。

### 在邮件配置前

- SMTP 服务商选择；
- 服务商 SMTP 参数；
- Secret 的安全交付渠道；
- SPF/DKIM/MAIL FROM 的准确 DNS 记录；
- DNS 修改权限或执行人。

### 在生产切换前

- 审核通过的 40 位 SHA；
- 维护窗口；
- 备份存储位置；
- 回滚负责人；
- stop-and-replace 切换授权；
- 是否授权下一轮服务器只读复核及后续写操作。

### 在 Owner 分配前

- 从真实查询结果中确认一个精确历史 Workspace ID；
- 对该单一 membership 变更的明确授权。

---

## 17. 推荐的任务拆分

为降低风险，建议不要在一个长任务中同时完成 review、SMTP/DNS、备份和生产切换。

推荐按以下任务拆分：

1. **代码冻结与提交任务**：只 review、修复 Gate、stage/commit/push/PR；
2. **SMTP 与 DNS 准备任务**：只选择服务商、生成准确 DNS 清单、验证传播；
3. **生产 release 准备任务**：只检出精确 SHA、生成 env、preflight、build、Caddy validate；
4. **备份与切换任务**：在批准维护窗口执行成对备份、恢复演练和 stop-and-replace；
5. **真实认证验收任务**：注册、验证、refresh、退出、密码重置；
6. **历史 Owner 分配任务**：只在批准一个真实 Workspace ID 后执行；
7. **观察与收尾任务**：健康观察、证据归档和最终验收。

每个任务都应独立确认授权边界，尤其是 Git 写操作、SSH、备份、容器停止、生产启动和数据库 membership 变更。

---

## 18. 下一任务可直接使用的提示词

### 18.1 首选：代码冻结、审核和提交准备

> 继续处理 `D:\Projects\polaris-commerce-studio-frontend-optimization` 的 `codex/login-system` 分支。先阅读 `docs/handoff-production-auth-next-steps-2026-08-08.md` 和 `docs/production-auth-deployment-2026-08-08.md`。本任务只做 Phase 0：冻结当前范围、审查完整 diff、检查 Secret、确认 `backend/skill_sources/**` 无改动、复核 Owner 脚本和 production Dockerfile 边界，并重跑必要 Gate。不要接入支付，不修改 Provider、生图、Amazon 或 Skill 能力。不要 reset/clean 当前工作树，也不要 commit、push、创建 PR 或部署，除非我在审核结果出来后明确授权。输出精确待提交文件清单、建议 commit 拆分和所有阻断问题。

### 18.2 后续：SMTP 与 DNS

> 继续处理 Polaris Commerce Studio 生产认证的 SMTP 与 DNS 准备。只依据邮件服务商实际提供的 SMTP、SPF、DKIM 和 MAIL FROM 值制定配置，不得杜撰。发件人固定为 `Polaris Commerce Studio <no-reply@mail.tengliapi.xyz>`。先发布并验证 SPF/DKIM，再发布 DMARC `p=none`，用多个公共解析器验证。不要读取或输出 Secret，不要部署，不要接入支付，不修改 `backend/skill_sources/**`、Provider、生图或 Amazon 能力。

### 18.3 后续：生产切换

> 继续处理 `D:\Projects\polaris-commerce-studio-frontend-optimization` 的生产认证切换。必须先阅读 `docs/handoff-production-auth-next-steps-2026-08-08.md`、`docs/production-auth-deployment-2026-08-08.md` 和 `deploy/README.md`。只允许使用已经 review、push 并批准的精确 40 位 SHA；服务器 release checkout 必须干净。先完成 production env、preflight、不可变镜像、Caddy validate、PostgreSQL + MinIO 成对备份和隔离恢复演练。internal 与 production 绝不能同时运行，禁止 `down -v`、volume prune 和带 volumes 的 system prune。任何 SSH 写操作、停止容器、备份、启动 production 或 Owner 分配都必须逐项获得明确授权。不要接入支付，不修改 Skill、Provider、生图或 Amazon 能力。

---

## 19. 关联文档

- `docs/handoff-login-system-2026-08-08.md`：登录系统接入前的原始交接背景；
- `docs/handoff-auth-domain-billing-production-consolidated-2026-08-08.md`：更早的认证、域名、Billing 和生产整体规划；
- `docs/production-auth-deployment-2026-08-08.md`：生产认证部署命令和运行手册；
- `deploy/README.md`：部署安全边界、paired-backup gate 和回滚要求；
- `deploy/compose.production.yml`：production 服务拓扑；
- `deploy/Caddyfile.production`：公网 HTTPS 入口；
- `deploy/generate_production_env.py`：服务器本地生产 env 生成器；
- `deploy/production_preflight.py`：fail-closed production preflight。

---

## 20. 当前交接停点

截至本文档创建时：

- 应用层登录系统已经完成；
- production 认证基础设施代码已经完成；
- 必要验证已有通过记录；
- 当前推荐立即执行的是 **Phase 0：冻结和完整审核**；
- 当前工作树仍未 stage、commit 或 push；
- 没有创建 PR；
- 没有执行生产切换；
- 没有配置真实 SMTP Secret；
- 没有发布或验证真实服务商 SPF/DKIM；
- 没有执行成对备份和恢复演练；
- 没有用 `1474953130@qq.com` 完成真实注册；
- 没有执行历史 Workspace Owner 分配；
- 没有接入支付；
- 没有修改 Skill 能力。
