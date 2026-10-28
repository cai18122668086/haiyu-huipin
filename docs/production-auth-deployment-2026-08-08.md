# Production authentication deployment runbook (2026-08-08)

本手册只覆盖 Polaris Commerce Studio 的生产认证基础设施：SuperTokens Core `v12.0.10`、标准 SMTP 事务邮件、`no-reply@mail.tengliapi.xyz`、`https://www.tengliapi.xyz` HTTPS 同源路由、完整认证冒烟，以及显式历史 Workspace Owner 分配。

不包含支付接入；不得修改 `backend/skill_sources/**`、Provider、生图或 Amazon 协议能力。

## 1. 文件与设计

| 文件 | 用途 |
| --- | --- |
| `deploy/compose.production.yml` | Production 栈；只有 Caddy 发布 80/443，SuperTokens 仅在私网。 |
| `deploy/Caddyfile.production` | `www.tengliapi.xyz` 自动 HTTPS、API/前端同源反代和安全响应头。 |
| `deploy/.env.production.example` | 仅展示变量名，禁止填入真实值后提交。 |
| `deploy/generate_production_env.py` | 在服务器生成 0600 env，不打印 Secret。 |
| `deploy/production_preflight.py` | 校验 env、不可变镜像、Provider 漂移、Compose/Caddy 拓扑及 Owner 脚本打包。 |
| `backend/scripts/assign_workspace_owner.py` | 只把一个已注册用户分配到一个明确历史 Workspace。 |

Backend 镜像只额外打包 `/app/scripts/assign_workspace_owner.py`，不打包全部运维脚本。

## 2. 2026-08-08 只读审计快照

服务器 `43.129.198.147`：`x86_64`、4 vCPU、7685 MiB 内存、根分区约 130230 MiB 可用；Docker `29.6.2`，Compose `5.3.1`。`/etc/polaris/polaris.env` 为 `root:root 0600`。当前 internal 容器健康，Caddy 只发布 `127.0.0.1:8080`，UFW 已允许 TCP 80/443。

现有数据卷：

- `polaris-internal-staging_postgres_data`
- `polaris-internal-staging_redis_data`
- `polaris-internal-staging_minio_data`

2026-08-08 通过 `1.1.1.1` 观察到：

```text
www.tengliapi.xyz -> 43.129.198.147
tengliapi.xyz     -> 104.244.95.188
```

Production Caddy 因此只声明 `www.tengliapi.xyz`，不得抢占 apex。审计时未发现 SPF、DMARC 或邮件服务商签发的 DKIM；切换前必须重新查询，因为 DNS 会变化。

## 3. 当前阻断项

以下全部解除前不得切换：

1. 所有登录与 production 基础设施改动已 review、commit、push。
2. 服务器工作树干净，并精确检出审核过的 40 位 SHA。
3. 已选择真实 SMTP 服务商并通过安全渠道取得凭据。
4. 服务商给出的 SPF/DKIM 已发布并传播。
5. DMARC 已发布。
6. PostgreSQL + MinIO 成对备份和隔离恢复演练成功。
7. 已批准 stop-and-replace 维护窗口。
8. `1474953130@qq.com` 尚未完成真实生产注册验证。
9. 尚未确认真实历史 Workspace ID，禁止猜测。

Production 显式复用 internal 的 PostgreSQL/Redis/MinIO 卷；两个项目绝不能同时运行。

## 4. SMTP 与 DNS

向事务邮件服务商索取：SMTP host、port、username、password/API credential、隐式 TLS 或 STARTTLS 模式、发信身份验证要求、准确 SPF、DKIM selector 与 TXT/CNAME、以及可能的自定义 MAIL FROM/bounce domain 记录。不得杜撰任何服务商值。

应用发件人固定为：

```text
Polaris Commerce Studio <no-reply@mail.tengliapi.xyz>
```

### SPF

只发布服务商给出的记录。每个 hostname 只能有一条有效 SPF；若已有 SPF，合并 mechanism，不得发布第二条 SPF。

### DKIM

只发布服务商提供的 selector 和准确 TXT/CNAME 目标，不得猜测，并记录轮换流程。

### DMARC

初期监控可发布：

```text
_dmarc.tengliapi.xyz TXT "v=DMARC1; p=none; adkim=r; aspf=r; pct=100"
```

确认真实邮件长期通过 SPF/DKIM alignment 后，再逐步升级到 `quarantine`、`reject`。

多解析器验证：

```bash
dig @1.1.1.1 TXT tengliapi.xyz +short
dig @8.8.8.8 TXT tengliapi.xyz +short
dig @1.1.1.1 TXT _dmarc.tengliapi.xyz +short
dig @8.8.8.8 TXT _dmarc.tengliapi.xyz +short
# 将 <selector> 替换为服务商给出的值；CNAME 型 DKIM 应查询服务商指定类型。
dig @1.1.1.1 TXT <selector>._domainkey.tengliapi.xyz +short
dig @8.8.8.8 TXT <selector>._domainkey.tengliapi.xyz +short
```

## 5. 精确 SHA 与生产 Secret

以下命令只是手册，不授权部署未审核工作树：

```bash
cd <SERVER_RELEASE_CHECKOUT>
git status --short
git rev-parse HEAD
git branch --show-current
```

要求 `git status --short` 无输出、HEAD 等于已 push/审核的 40 位 SHA，且可从批准的远端引用到达。

在服务器交互生成 production env：

```bash
sudo python3 deploy/generate_production_env.py \
  --base-env /etc/polaris/polaris.env \
  --output /etc/polaris/polaris.production.env \
  --release-sha <REVIEWED_40_CHARACTER_SHA>
```

生成器会保留数据库、Redis、MinIO、签名、S3 和 Provider 配置；生成新的合法 SuperTokens API key；隐藏读取 SMTP 密码；固定发件地址；写入 0600；不打印值。默认拒绝覆盖已有文件，非必要不要使用 `--force`。

只验证权限，不读取内容：

```bash
sudo chown root:root /etc/polaris/polaris.production.env
sudo chmod 0600 /etc/polaris/polaris.production.env
sudo stat -c '%U:%G %a %n' /etc/polaris/polaris.production.env
```

禁止对 populated env 执行 `cat`、`grep`、`env`、`set -x`，也不要运行会完整渲染配置的 `docker compose config`。

执行 fail-closed preflight：

```bash
sudo python3 deploy/production_preflight.py \
  --env-file /etc/polaris/polaris.production.env \
  --base-env /etc/polaris/polaris.env

sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  config --quiet
```

Provider 漂移检查只输出发生变化的变量名，不输出值。

## 6. 镜像与供应链 Gate

批准的 SuperTokens `linux/amd64` 引用固定为：

```text
supertokens/supertokens-postgresql:12.0.10@sha256:4516ec7c00b8fb2a773012694d8ebf03b54799d59cfc974160590f16d235ce72
```

切换前必须：拉取 env 中所有 digest-pinned 基础/基础设施镜像；只用干净审核 SHA 构建；应用镜像精确标记为 `polaris/backend:<SHA>` 和 `polaris/frontend:<SHA>`；记录 image ID/digest、SBOM/扫描证据；重新运行 preflight。

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  build --pull=false api frontend
```

禁止 `latest`、浮动 tag、脏 build context 或与 `git rev-parse HEAD` 不一致的 SHA。

用同一 Caddy 镜像校验实际挂载文件：

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  run --rm --no-deps --entrypoint caddy caddy \
  validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Host 侧 CRLF 管道产生的 format warning 不等于 validate 失败，应以上述容器内实际文件结果为准。

## 7. 成对备份与切换

切换前先阻断入口和全部 writer，创建 PostgreSQL custom-format dump；停止 MinIO writer 后完整快照 MinIO 卷；记录 UTC 时间、release SHA、schema revision、image ID、行数/对象数、字节数和 SHA-256；复制到独立批准存储；在新的临时项目/卷中恢复两者并验证。只备份数据库或只备份对象均不完整。必须使用 `deploy/README.md` 中已经演练的 paired-backup gate，不得临场对生产卷试验命令。

备份完成后停止 internal，但不删除卷：

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.env \
  -f deploy/compose.internal.yml \
  stop
```

确认共享卷没有任何使用者：

```bash
for volume in \
  polaris-internal-staging_postgres_data \
  polaris-internal-staging_redis_data \
  polaris-internal-staging_minio_data
do
  sudo docker ps --filter volume="$volume" --format '{{.ID}} {{.Names}}'
done
```

三个查询都必须无输出，否则立即停止切换。

从审核 release checkout 启动：

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

Migration 必须成功完成，所有长运行服务必须 healthy 且无 restart loop。

永远禁止：

```text
docker compose down -v
docker volume prune
docker system prune --volumes
```

## 8. 公网认证冒烟

使用普通浏览器和 DevTools，不在终端或截图中记录密码。

基础设施：

- `https://www.tengliapi.xyz` 证书有效，HTTP 跳转 HTTPS。
- `/health/live`、`/health/ready` 经 Caddy 正常。
- PostgreSQL、Redis、MinIO、API、frontend、SuperTokens 均无公网监听。
- 3567 只在 Docker 私网可达。

用 `1474953130@qq.com`：

1. 打开 `/auth/register`，使用未复用的新密码注册。
2. UI 必须要求邮箱验证。
3. 验证邮件 From 为 `no-reply@mail.tengliapi.xyz`，链接必须是 `https://www.tengliapi.xyz`。
4. 完成验证并进入 `/account`，确认显示真实邮箱。
5. 首次触发需要 Workspace 的页面/API，确认创建个人 Workspace、用户为 Owner、初始积分为 `0`。
6. 检查 Cookie 为 `Secure`、`HttpOnly`，预期 `SameSite=Lax`；localStorage/sessionStorage 无 token。
7. 等待/触发 access token 过期，确认 Session 自动 refresh。
8. 退出后 protected API 返回 `401`；未验证账号返回 `403 EMAIL_NOT_VERIFIED`。
9. 从 `/auth/forgot-password` 发起重置；重置链接 host/HTTPS 正确；旧密码失败、新密码成功；再完成一次退出/登录。

保留非 Secret 证据：投递时间、From/recipient、可安全保存的 provider event ID、SPF/DKIM/DMARC 结果、链接 host。完整邮件头如含敏感信息必须先脱敏。

## 9. 显式历史 Workspace Owner 分配

只有当用户已成功登录并产生本地 JIT user、备份有效、且真实 Workspace ID 已独立确认后才能执行。

先列出候选项，不猜测：

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  exec -T postgres \
  psql -U polaris -d polaris -c \
  "SELECT id, name, status, created_at FROM workspaces ORDER BY created_at, id;"
```

审批一个精确 ID 后：

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  exec -T api \
  python scripts/assign_workspace_owner.py \
  --email '1474953130@qq.com' \
  --workspace-id '<APPROVED_HISTORICAL_WORKSPACE_ID>'
```

脚本只修改对应 `workspace_members`，不会改 workspace creator、历史素材、任务或其他外键；幂等返回 `created`、`restored` 或 `unchanged`。

验证：

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.production.env \
  -f deploy/compose.production.yml \
  exec -T postgres \
  psql -U polaris -d polaris -c \
  "SELECT wm.workspace_id, u.email_normalized, wm.role, wm.status FROM workspace_members wm JOIN users u ON u.id = wm.user_id WHERE u.email_normalized = '1474953130@qq.com' ORDER BY wm.workspace_id;"
```

误分配回滚必须基于变更前证据，对那一条 membership 做单独审核的显式恢复；禁止通过删除用户、Workspace、素材或任务回滚。

## 10. 回滚与验收

若 migration、HTTPS、邮件或认证冒烟失败：停止 Caddy 和 production writer，保留日志及脱敏证据；先判断 schema 向后兼容性。兼容时可停止 production 并用原精确镜像/env 恢复 internal；不兼容时按批准方案恢复成对 PostgreSQL + MinIO 备份，禁止盲目 `alembic downgrade`。始终确认只有一个项目连接共享卷。

最终记录：审核 SHA/远端引用、image ID/digest、preflight、备份与恢复演练、SPF/DKIM/DMARC、证书、注册/验证/登录/refresh/退出/重置、批准 Workspace ID、Owner 结果、服务健康/监听和回滚决定。

同时确认：无支付接入；`backend/skill_sources/**` 无改动；Provider 配置无漂移；生图/Amazon 能力无改动；Git、日志和证据中无 Secret。
