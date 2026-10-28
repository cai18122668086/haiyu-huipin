# Polaris Commerce Studio 登录系统接入——完整交接说明

> 交接日期：2026 年 8 月 8 日（星期六）
> 当前用途：交给新的 Codex 任务，继续设计和接入真实登录系统
> 当前代码基线：`main` / `ac9bfb8192f190b79d94bf1a28c7d9ef47c64163`

---

## 1. 一句话结论

网站业务功能和 Amazon 视觉套图链路已经部署并通过真实测试，但**当前运行环境仍是单一测试身份的内部测试环境，并没有真实登录系统**。

登录系统接入时应当：

1. 从最新 `main` 创建短生命周期功能分支；
2. 优先选择成熟的外部身份供应商或标准 OIDC/OAuth2，而不是仓促自建密码系统；
3. 将外部身份安全映射到现有 `users`、`workspaces`、`workspace_members` 数据模型；
4. 明确迁移当前测试工作区及其 282 个素材、65 个任务的归属；
5. 完成认证、数据隔离、前端路由保护、OpenAPI 和部署配置后，才将 `POLARIS_AUTH_MODE` 切换为 `external`；
6. 当前 `deploy/compose.internal.yml` 是仅允许 SSH 隧道访问的 internal staging 配置，不能直接当作公网生产登录部署方案。

---

## 2. 唯一应使用的主工作目录

```text
D:\Projects\polaris-commerce-studio-frontend-optimization
```

当前状态：

```text
分支：main
HEAD：ac9bfb8192f190b79d94bf1a28c7d9ef47c64163
上游：origin/main
差异：0 / 0
工作区：除本交接文档外无未提交修改
```

远端仓库：

```text
https://github.com/cai18122668086/polaris-commerce-studio.git
```

另一个 worktree：

```text
D:\Projects\polaris-commerce-studio
```

它仍绑定分支：

```text
codex/p0-delivery-filename-progress
```

不要在未解除该 worktree 绑定前强制删除对应本地分支，也不要同时在两个 worktree 修改同一批认证文件。

---

## 3. 分支、提交和推送策略

### 3.1 发布基线

从现在开始：

```text
main = 唯一正式发布基线
```

不要再从历史 staging 分支直接部署。

### 3.2 登录系统开发分支

推荐在主工作目录执行：

```powershell
git switch main
git pull --ff-only origin main
git status --short --branch
git switch -c codex/login-system
```

开发完成后：

```powershell
git add <明确需要提交的文件>
git diff --cached --check
git commit -m "feat: integrate external authentication"
git push -u origin codex/login-system
```

经过审查和真实测试后，再以线性历史合并回 `main`：

```powershell
git switch main
git pull --ff-only origin main
git merge --ff-only codex/login-system
git push origin main
```

如果 `main` 已经产生新提交，应先在功能分支上同步和解决冲突，不能对 `main` 强推。

### 3.3 部署分支规则

**只部署已经进入远端 `main` 的确定提交 SHA。**

不要部署：

- 未提交的工作区；
- 仅存在于本地的提交；
- 历史 staging 分支；
- merge commit 未审查的组合版本；
- 用 `git push --force` 改写过的发布历史。

部署前必须确认：

```powershell
git fetch origin --prune
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count origin/main...HEAD
```

期望当前位于 `main`，工作区干净，并且差异为：

```text
0  0
```

---

## 4. 当前正式运行版本

当前服务器：

```text
ubuntu@43.129.198.147
```

当前本机授权 SSH key 路径：

```text
D:\Users\Administrator\Desktop\fwq.pem
```

安全要求：

- 绝对不要输出 key 内容；
- 不要把 key 复制到仓库或服务器发布目录；
- 不要把 `/etc/polaris/polaris.env` 内容粘贴到对话、日志或 Git；
- 后续应尽量为不同维护者使用独立 SSH 账号和 key。

当前发布提交：

```text
ac9bfb8192f190b79d94bf1a28c7d9ef47c64163
```

当前发布目录：

```text
/opt/polaris/releases/ac9bfb8192f190b79d94bf1a28c7d9ef47c64163
```

当前没有 `/opt/polaris/current` 软链接；Compose 直接从上述不可变发布目录运行。

Compose 项目名：

```text
polaris-internal-staging
```

Compose 文件：

```text
/opt/polaris/releases/<SHA>/deploy/compose.internal.yml
```

服务器环境文件：

```text
/etc/polaris/polaris.env
```

权限要求：

```text
root:root
0600
```

应用镜像命名规则：

```text
polaris/backend:<40位提交SHA>
polaris/frontend:<40位提交SHA>
```

当前镜像：

```text
polaris/backend:ac9bfb8192f190b79d94bf1a28c7d9ef47c64163
polaris/frontend:ac9bfb8192f190b79d94bf1a28c7d9ef47c64163
```

当前服务：

```text
postgres
redis
minio
migrate
api
worker
dispatcher
frontend
caddy
```

除一次性 `migrate` 外，其他长期服务当前均健康。

当前数据库迁移：

```text
20260808_0006 (head)
```

---

## 5. 当前部署环境的真实定位

虽然日常沟通中称为“正式服务器”，但技术上当前配置仍是：

```text
internal staging / 单测试用户环境
```

当前 `/api/v1/runtime` 返回：

```json
{
  "environment": "development",
  "authentication_mode": "development",
  "task_runner": "tengli"
}
```

当前外部认证能力：

```json
{
  "availability": "unavailable",
  "execution": "none",
  "reason_code": "EXTERNAL_AUTHENTICATION_NOT_IMPLEMENTED"
}
```

网络边界：

- Caddy 只监听服务器 `127.0.0.1:8080`；
- PostgreSQL、Redis、MinIO、API、Worker、Dispatcher、Frontend 都没有公开宿主机端口；
- 当前访问方式是 SSH 隧道；
- 当前没有公网域名、TLS 终止和面向公网的登录入口。

SSH 隧道示例：

```powershell
ssh -i "D:\Users\Administrator\Desktop\fwq.pem" `
  -N -L 8080:127.0.0.1:8080 `
  ubuntu@43.129.198.147
```

然后访问：

```text
http://127.0.0.1:8080
```

**不要因为接入了登录页面，就直接把当前 Caddy 改为监听 `0.0.0.0:8080`。**

如果要公开给真实用户，必须单独设计并验证：

- 正式域名；
- HTTPS/TLS；
- 公网 Caddy/反向代理配置；
- 云防火墙和宿主机防火墙；
- Cookie 安全属性；
- CORS/CSRF；
- 登录回调 URL；
- 限流、审计和告警；
- production 环境配置。

---

## 6. 当前认证后端现状

主要文件：

```text
backend/app/auth.py
backend/app/config.py
backend/app/models.py
backend/app/routes_v1.py
backend/app/schemas_v1.py
backend/app/main.py
```

### 6.1 当前身份模式

配置项：

```text
POLARIS_AUTH_MODE=development | external
```

当前 Compose 固定为：

```text
POLARIS_AUTH_MODE=development
```

`backend/app/auth.py` 当前逻辑：

- `development` 模式：
  - 默认使用 `POLARIS_DEVELOPMENT_USER_ID`；
  - 也接受客户端传入的 `X-User-Id`；
  - 启动时自动确保测试用户、测试工作区、Owner 成员关系、额度表存在。
- `external` 模式：
  - 尚未验证任何 Token、Session 或 Cookie；
  - 所有请求直接返回：

```text
HTTP 401
UNAUTHENTICATED
请求未通过身份认证。
```

因此不能只修改环境变量为：

```text
POLARIS_AUTH_MODE=external
```

否则全站 API 会立即全部变成 401。

### 6.2 当前 Principal

当前主体模型非常简单：

```python
@dataclass(frozen=True)
class Principal:
    user_id: str
```

后续可以保留该核心边界，但真实认证适配器必须先安全验证外部凭证，再生成可信 `Principal`。

外部模式下绝对不能继续信任浏览器直接传入：

```text
X-User-Id
```

### 6.3 当前用户与工作区数据模型

数据库已经存在：

```text
users
workspaces
workspace_members
workspace_limits
workspace_credit_accounts
```

`users` 已具备：

```text
id
auth_provider
auth_subject
email_normalized
display_name
status
last_login_at
```

并且有唯一约束：

```text
(auth_provider, auth_subject)
```

这非常适合映射外部 OIDC/OAuth 身份，不需要重新设计整套用户主表。

工作区权限已经通过：

```text
workspace_members(workspace_id, user_id, role, status)
```

支持：

```text
owner
member
viewer
```

现有 API 已在工作区边界检查有效成员关系；无权限时统一返回 `WORKSPACE_NOT_FOUND`，可以减少工作区枚举泄露。

### 6.4 当前身份 API

已经存在：

```text
GET /api/v1/me
GET /api/v1/workspaces
GET /api/v1/workspaces/{workspace_id}
```

当前 `/api/v1/me` 只返回：

```json
{
  "id": "<user_id>"
}
```

真实登录接入后通常需要扩展为：

```text
id
email
display_name
avatar_url（如果需要）
auth_provider
```

如果修改响应模型，必须同步 OpenAPI 和前端生成类型。

---

## 7. 当前前端认证现状

主要文件：

```text
lib/api/client.ts
lib/api/request.ts
lib/api/api-error.ts
lib/api/error-mapper.ts
features/workspaces/api/workspaces.ts
features/runtime/api/runtime.ts
app/providers.tsx
components/app-shell.tsx
components/top-navigation.tsx
```

### 7.1 API Client

当前 `lib/api/client.ts`：

- 默认使用同源 `/api`；
- 本地开发可使用 `NEXT_PUBLIC_API_BASE_URL`；
- 只自动加入 `X-Request-Id`；
- 不加入 Authorization；
- 没有显式 `credentials: "include"`；
- 没有统一的 401 登录跳转处理。

如果使用 Cookie Session，需要：

- 请求携带 Cookie；
- 后端按精确来源允许 credentials；
- Cookie 使用合适的 `HttpOnly`、`Secure`、`SameSite`；
- 对状态修改请求进行 CSRF 防护。

如果使用 Bearer Token，需要：

- 在 API Client 中安全注入 `Authorization: Bearer ...`；
- 后端 CORS 允许 `Authorization`；
- 正确处理刷新、过期和撤销；
- 不将长期凭证写入不安全的 `localStorage`。

### 7.2 当前工作区选择

`features/workspaces/api/workspaces.ts` 当前直接使用：

```text
用户工作区列表中的第一个工作区
```

多用户登录后必须明确：

- 首次登录是否自动创建个人工作区；
- 多工作区如何选择；
- 当前工作区如何持久化；
- 用户失去成员资格后如何清理缓存和跳转。

### 7.3 ChatGPT Auth 文件

仓库中存在：

```text
app/chatgpt-auth.ts
```

该文件读取以下托管环境请求头：

```text
oai-authenticated-user-email
oai-authenticated-user-full-name
```

但它目前没有被其他页面引用，是一个未接入业务 API 的托管环境辅助文件，**不是现有真实登录系统**。

除非明确部署在提供并可信签名这些请求头的平台后面，否则不能直接把这些请求头当作公网认证凭据。

### 7.4 当前缺失的前端能力

目前没有完整实现：

- 登录页；
- 注册页；
- 邮箱验证；
- 忘记密码；
- 重置密码；
- OAuth/OIDC 回调页；
- 登录 Session Provider；
- 路由保护；
- 统一 401 跳转；
- 退出登录；
- 登录后清理/隔离 React Query 缓存；
- 用户资料和工作区切换 UI。

---

## 8. 现有数据迁移是登录接入的关键风险

截至 2026 年 8 月 8 日，正式测试数据库快照：

```text
users=2
workspaces=1
workspace_members=1
assets=282
jobs=65
```

当前测试用户：

```text
user_id=681b2cb3-5e6e-48ce-9476-ea6af80a273f
auth_provider=development
status=active
```

当前测试工作区：

```text
workspace_id=dc3f34ea-8733-4d0b-bda3-f8d192843922
```

当前成员关系：

```text
上述 development 用户是该工作区 owner
```

该工作区当前包含：

```text
282 个素材
65 个任务
```

数据库还有迁移占位用户：

```text
usr_legacy / migration / legacy
```

### 切换真实登录前必须决定

1. 当前测试工作区是否继续保留；
2. 哪一个真实管理员账号需要访问当前数据；
3. 是给真实用户新增 `workspace_members` Owner 关系，还是迁移原 development 用户身份；
4. 是否保留旧 development 用户用于审计和 `created_by_user_id` 外键；
5. 新注册用户是否自动创建独立工作区；
6. 是否允许邀请成员加入已有工作区。

推荐的低风险方案：

- 保留原 development 用户记录，不删除；
- 第一个明确授权的真实管理员登录并完成 JIT provisioning；
- 通过明确、可审计的迁移给该真实用户增加当前工作区 Owner 成员关系；
- 不批量改写历史任务和素材的创建者字段；
- 新用户默认获得自己的新工作区，除非通过邀请加入现有工作区。

如果忽略这一点，切换 external 后新用户可以成功登录，但 `/api/v1/workspaces` 会返回空列表，页面看起来像“所有数据都丢失了”。

---

## 9. 推荐的认证实现边界

### 9.1 首选方案

为了尽快上线且保持安全和可维护性，优先考虑：

```text
成熟外部身份供应商 + OIDC/OAuth2 + 服务端验证 + 现有 users/workspaces 映射
```

例如选择支持以下能力的供应商：

- 邮箱密码或免密码登录；
- 邮箱验证；
- 忘记密码；
- 可选社交登录；
- OIDC/JWT/JWKS；
- Session 撤销；
- 登录审计和风控。

具体供应商尚未确定，下一任务开始前应先确认：

- 使用哪一家身份服务；
- 登录方式；
- 是否需要微信、Google 等社交登录；
- 是否需要邀请制；
- 是否允许自由注册；
- 是否准备公开域名和回调 URL；
- 是否需要多工作区和团队成员。

### 9.2 如果自建邮箱密码系统

必须同时实现并审查：

- 强密码哈希（Argon2id 或同等级方案）；
- 邮箱验证 Token；
- 密码重置 Token；
- Session 存储和撤销；
- 登录失败限流；
- 防撞库；
- CSRF；
- Cookie 安全属性；
- 密钥轮换；
- 审计日志；
- 邮件投递；
- 账户禁用和恢复。

不能只增加一张 `password` 字段和一个登录接口就上线。

---

## 10. 后端建议实施顺序

1. 确定认证供应商和凭证形式；
2. 定义认证配置和 Secret 管理；
3. 在 `backend/app/auth.py` 增加真实认证适配器；
4. 验证 Token/Session 的签名、issuer、audience、expiry、nonce 等；
5. 按 `(auth_provider, auth_subject)` 查找或 JIT 创建 `UserRecord`；
6. 检查 `UserStatus.ACTIVE`；
7. 更新 `email_normalized`、`display_name`、`last_login_at`；
8. 生成可信 `Principal`；
9. 外部模式完全禁止 `X-User-Id` 身份伪造；
10. 实现首次登录工作区创建或邀请加入策略；
11. 扩展 `/api/v1/me`；
12. 保持所有现有 workspace membership 校验；
13. 增加认证审计事件；
14. 增加必要数据库迁移；
15. 更新 OpenAPI 和前端类型。

不要把身份验证逻辑散落在每条业务路由中；应继续通过 FastAPI Dependency 集中处理。

---

## 11. 前端建议实施顺序

1. 增加认证 Provider/Session 上下文；
2. 实现登录、回调、退出和错误页面；
3. 在 API Client 中统一注入 Cookie 或 Bearer Token；
4. 增加统一 401/Session 过期处理；
5. 保护 Dashboard、Assets、Jobs 和全部 Workspace 页面；
6. 登录用户变化时清理 React Query 缓存；
7. 加载 `/api/v1/me`；
8. 实现无工作区、单工作区和多工作区状态；
9. 登录后恢复安全的相对 return URL；
10. 退出后清理用户相关状态并跳转；
11. 不允许开放重定向；
12. 不在客户端保存长期 Secret。

---

## 12. OpenAPI 同步要求

如果修改身份接口、请求头、安全方案或用户响应模型，必须运行：

```powershell
npm run api:export
npm run api:generate
npm run api:check
```

相关文件：

```text
docs/openapi-v1.json
lib/api/generated/openapi.d.ts
```

不要手工修改生成的 TypeScript API 类型后却不更新 OpenAPI 源。

---

## 13. 登录系统必要测试范围

目标是做必要的辟谣测试，不陷入无限审计，但以下边界不能省略。

### 13.1 后端

至少覆盖：

- 未登录请求返回 401；
- 有效凭证成功；
- 过期、错误签名、错误 issuer/audience 被拒绝；
- `X-User-Id` 在 external 模式无法冒充用户；
- disabled 用户被拒绝；
- 首次登录重复请求不会创建重复用户；
- `(auth_provider, auth_subject)` 唯一性；
- 用户只能看到自己的工作区；
- 非成员无法读取或写入其他工作区；
- viewer 不能执行写操作；
- 登录后当前历史工作区仍可访问；
- 登出或 Session 撤销后无法继续调用 API；
- `/me` 和 `/workspaces` 合同正确。

### 13.2 前端

至少覆盖：

- 未登录访问受保护页面跳转登录；
- 登录后返回原安全页面；
- 401 统一跳转；
- 退出后缓存清空；
- return URL 不允许外部地址；
- 登录状态下工作区和现有任务正常加载；
- 刷新页面后登录状态不丢失；
- Session 过期有明确提示。

### 13.3 回归

登录修改不能破坏：

- 素材上传；
- 任务创建、轮询、重试、取消和下载；
- Amazon Visual Suite；
- 其他生图入口；
- MinIO 同源下载代理；
- Provider 执行和任务队列。

不需要为了登录测试重新生成完整 21 图套件；使用无付费或最小真实回归即可。

---

## 14. 本地必要命令

### 前端

```powershell
npm run typecheck
npm run lint
npm test
```

### 后端

使用项目虚拟环境：

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
.\backend\.venv\Scripts\python.exe -m ruff check backend/app backend/tests
.\backend\.venv\Scripts\python.exe -m mypy backend/app
```

开发过程中可以先运行认证相关定向测试，提交前再运行与改动范围匹配的必要回归。

### 部署源检查

```powershell
python deploy/preflight.py --mode source
python -m unittest discover -s deploy/tests -v
git diff --check
git status --short --branch
```

---

## 15. 标准发布流程

以下流程是操作模板，不代表可以跳过部署前真实测试。

### 15.1 本地确认发布 SHA

```powershell
Set-Location "D:\Projects\polaris-commerce-studio-frontend-optimization"
git switch main
git pull --ff-only origin main
git status --short --branch
$sha = git rev-parse HEAD
Write-Host $sha
```

必须确认工作区干净、`HEAD == origin/main`。

### 15.2 生成不可变发布包

避免通过 PowerShell 文本管道传输 tar 或多行 Bash；此前曾因 CRLF 导致远端把 `docker compose ps\r` 识别为错误命令。

推荐生成实体 tar 文件：

```powershell
$archive = "$env:TEMP\polaris-$sha.tar"
git archive --format=tar -o $archive $sha
scp -i "D:\Users\Administrator\Desktop\fwq.pem" `
  $archive `
  "ubuntu@43.129.198.147:/tmp/polaris-$sha.tar"
```

### 15.3 服务器创建发布目录

```bash
sudo mkdir -p "/opt/polaris/releases/$SHA"
sudo chown ubuntu:ubuntu "/opt/polaris/releases/$SHA"
tar -xf "/tmp/polaris-$SHA.tar" -C "/opt/polaris/releases/$SHA"
```

确认源码来自精确 Git SHA，不要直接复制一个可能含未提交文件的本地目录。

### 15.4 环境文件备份和更新

先备份：

```bash
sudo cp --preserve=mode,ownership,timestamps \
  /etc/polaris/polaris.env \
  "/etc/polaris/polaris.env.before-$SHA"
```

使用 `sudoedit` 更新：

```text
POLARIS_RELEASE_SHA=<SHA>
POLARIS_BACKEND_IMAGE=polaris/backend:<SHA>
POLARIS_FRONTEND_IMAGE=polaris/frontend:<SHA>
```

认证上线时还需要根据已完成实现更新认证相关变量，但不能提前把当前环境切换成 `external`。

### 15.5 Preflight

在新发布目录运行：

```bash
cd "/opt/polaris/releases/$SHA"
python3 deploy/preflight.py --mode source
python3 -m unittest discover -s deploy/tests -v
```

然后按服务器实时资源数据运行 deployment preflight：

```bash
python3 deploy/preflight.py \
  --mode deployment \
  --env-file /etc/polaris/polaris.env \
  --host-memory-mib <实时测量值> \
  --host-cpus <实时测量值> \
  --free-disk-mib <实时测量值> \
  --free-inode-percent <实时测量值> \
  --candidate-images-mib <实时测量值> \
  --paired-backup-mib <实时测量值>
```

当前服务器容量快照仅供参考，不能替代下一次实时测量：

```text
CPU：4
内存：约 7685 MiB
Docker 文件系统可用：约 130254 MiB
可用 inode：约 89%
长期服务上限：2864 MiB / 3.20 CPU
```

任何 `BLOCKED` 结果都停止部署。

### 15.6 Compose 配置检查

```bash
cd "/opt/polaris/releases/$SHA"
sudo docker compose \
  --env-file /etc/polaris/polaris.env \
  -f deploy/compose.internal.yml \
  config --quiet
```

安全说明：日常发布不要运行不带 `--quiet` 的 `docker compose config`，因为完整渲染结果可能展开并打印环境变量。网络、端口和服务结构应优先检查受版本控制的 `deploy/compose.internal.yml`；如确需查看完整结果，只能在服务器受控终端临时查看，不得复制到对话、日志、工单或普通文件。

必须确认：

- 只有 Caddy 发布 `127.0.0.1:8080`；
- 数据库、Redis、MinIO、API、Worker、Dispatcher、Frontend 无宿主机公开端口；
- Worker 才能访问 provider-egress；
- API/Worker/Dispatcher 的功能开关符合预期；
- Secret 没有被打印或复制到外部记录。

### 15.7 构建镜像

```bash
cd "/opt/polaris/releases/$SHA"
sudo docker compose \
  --env-file /etc/polaris/polaris.env \
  -f deploy/compose.internal.yml \
  build api frontend
```

Backend 镜像由 API 的共享 build 定义生成，Worker、Dispatcher、Migrate 使用相同不可变 backend 镜像。

### 15.8 启动新版本

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.env \
  -f deploy/compose.internal.yml \
  up -d --no-build --pull never
```

禁止使用：

```text
docker compose down -v
docker volume rm
docker volume prune
docker system prune --volumes
```

### 15.9 发布后验证

```bash
docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}'
curl -i http://127.0.0.1:8080/health/live
curl -i http://127.0.0.1:8080/health/ready
```

确认：

- API、Worker、Dispatcher、Frontend、Caddy healthy；
- 使用新 SHA 镜像；
- migration 成功；
- 没有 restart loop；
- `alembic current` 是预期 head；
- 日志没有新增 Traceback/ERROR；
- 登录改动上线时，匿名、登录、退出、工作区数据访问均真实测试通过。

登录上线后的健康检查不能只看 `/health/ready`，必须实际测试：

1. 未登录跳转；
2. 登录成功；
3. `/api/v1/me`；
4. `/api/v1/workspaces`；
5. 当前历史工作区和素材可见；
6. 退出后 API 失效。

---

## 16. 回滚原则

发布前保留：

- 上一个发布目录；
- 上一个 backend/frontend 镜像；
- 上一个 `/etc/polaris/polaris.env` 备份；
- 当前 Alembic revision；
- PostgreSQL 与 MinIO 配对备份。

如果新迁移向后兼容，可以将环境中的镜像引用切回上一 SHA，再执行：

```bash
sudo docker compose \
  --env-file /etc/polaris/polaris.env \
  -f <上一发布目录>/deploy/compose.internal.yml \
  up -d --no-build --pull never
```

不要盲目执行：

```text
alembic downgrade
docker compose down -v
删除 PostgreSQL/MinIO volume
```

认证迁移尤其要避免删除旧用户、成员关系或 Session 审计数据。

---

## 17. 当前 Provider 和 Amazon 能力边界

正式测试环境当前：

```text
POLARIS_TASK_RUNNER=tengli
POLARIS_PROVIDER_QUOTA_ENABLED=false
POLARIS_WORKSPACE_CREDIT_ENFORCEMENT_ENABLED=false（当前 internal test 设计）
POLARIS_AMAZON_VISUAL_SUITE_PROVIDER_ENABLED=true
```

登录系统接入不能自作主张：

- 重新开启测试账号 Provider quota；
- 修改 Provider reservation 语义；
- 修改 Amazon Planner、Prompt、Review、重绘或生图能力；
- 修改 `backend/skill_sources/**`；
- 修改本机 Codex 全局 skill；
- 将登录套餐、积分或计费系统与身份认证强耦合。

身份认证解决“是谁”；额度/计费解决“能使用多少”，应保持可独立配置。

---

## 18. 已知问题和已确认事实

### 18.1 JPG/JPEG

真正的 JPG/JPEG、PNG、WebP 可以上传。

此前测试文件：

```text
D:\Users\Administrator\Desktop\电商素材调查\素材图片\1.jpg
```

虽然扩展名是 `.jpg`，真实内容是 AVIF，文件头含 `ftypavif`。

没有上线 AVIF 自动转 JPEG 的兼容代码；当前上传协议保持不变。

### 18.2 两个开发日志

```text
.vinext-dev.stdout.log
.vinext-dev.stderr.log
```

它们是本地 Vinext/Vite 开发服务器输出，已经加入 `.gitignore`，不应提交。

### 18.3 README 可能落后

根目录 `README.md` 中仍有“没有真实登录、支付或部署”等早期阶段描述，其中“没有真实登录”仍正确，但“没有部署/真实 Provider”已经不符合当前服务器现状。

涉及发布和运行状态时，以以下内容为准：

1. 当前代码；
2. `deploy/README.md`；
3. `deploy/compose.internal.yml`；
4. 本交接文档；
5. 正式服务器实际容器和 `/api/v1/runtime`。

---

## 19. 历史分支现状

`main` 已包含当前正式发布版本。

仍有 5 个远端历史分支未合并到当前 `main`：

```text
origin/codex/internal-beta-staging-v11
origin/codex/internal-beta-staging-v12
origin/codex/internal-beta-staging-v13
origin/codex/internal-staging-v10f
origin/codex/internal-staging-v10h-final3
```

不要为了“整理仓库”把这些分支盲目合并进登录系统分支，也不要在登录开发期间批量删除。

它们不是登录系统的基础；登录开发只从最新 `main` 开始。

---

## 20. 严禁操作

```text
git push --force origin main
git reset --hard（存在未保存工作时）
git clean -fd（未检查未跟踪文件时）
docker compose down -v
docker volume rm
docker volume prune
docker system prune --volumes
删除 PostgreSQL、Redis、MinIO 数据卷
输出或提交 SSH 私钥
输出或提交 /etc/polaris/polaris.env
修改 backend/skill_sources/**
直接公开当前 internal Caddy 端口
仅切换 POLARIS_AUTH_MODE=external 而不实现认证
```

---

## 21. 新 Codex 任务建议首轮执行

```powershell
Set-Location "D:\Projects\polaris-commerce-studio-frontend-optimization"
git status --short --branch
git fetch origin --prune
git rev-parse HEAD
git rev-parse origin/main
git worktree list
```

然后阅读：

```text
docs/handoff-login-system-2026-08-08.md
backend/app/auth.py
backend/app/models.py
backend/app/config.py
backend/app/routes_v1.py
backend/app/main.py
lib/api/client.ts
features/workspaces/api/workspaces.ts
deploy/README.md
deploy/compose.internal.yml
deploy/Caddyfile
```

开始改代码前，先和用户确认：

1. 选择哪一个登录供应商；
2. 允许注册还是邀请制；
3. 登录方式；
4. 是否已有正式域名；
5. 当前 282 个素材和 65 个任务归属哪个真实账号；
6. 是否需要多工作区和成员邀请；
7. 登录系统先在 SSH 内部环境测试，还是同步建设公网 production profile。

---

## 22. 完成标准

登录系统只有满足以下条件才算可部署：

- [ ] 外部凭证经过服务端可信验证；
- [ ] external 模式不信任 `X-User-Id`；
- [ ] 用户映射到现有 `users`；
- [ ] 工作区成员关系和数据隔离正确；
- [ ] 当前历史工作区数据有明确归属；
- [ ] 登录、刷新、过期、退出完整；
- [ ] 前端受保护路由和 401 处理完整；
- [ ] OpenAPI 和生成类型同步；
- [ ] 数据库迁移可审查、可备份、可回滚；
- [ ] 必要认证测试和业务回归通过；
- [ ] 未修改任何 Skill 或生图能力；
- [ ] 代码进入 `main` 后才按精确 SHA 部署；
- [ ] 部署后实际登录和历史数据访问通过；
- [ ] 如果公网开放，HTTPS、Cookie、CORS、CSRF、防火墙和回调 URL 全部正确。

---

# 最终交接结论

当前项目适合在现有用户/工作区数据模型上接入标准外部认证，但不能把它误认为“只差一个登录页面”。真正的关键工作是：

```text
可信身份验证
→ 用户映射/JIT provisioning
→ 工作区成员关系
→ 现有数据归属迁移
→ 前端登录态和路由保护
→ OpenAPI
→ 安全部署配置
```

登录开发从 `main` 开始，使用 `codex/login-system` 短分支；审查和测试通过后 fast-forward 回 `main`；正式部署只部署 `main` 上的精确提交 SHA。
