# 海宇绘品

面向电商商家的 AI 图片工作台，提供商品图、详情图、套图、一键复刻、任务与素材管理，以及独立的账户、会员、积分和支付后台。运营主体为海宇数科（广东横琴）科技有限公司。

- 唯一正式源码仓库：<https://github.com/cai18122668086/haiyu-huipin>（私有）。
- 正式网站：<https://huipin.haiyushuke.com>。
- 账户中心：<https://huipin.haiyushuke.com/account>。
- 管理后台：<https://huipin.haiyushuke.com/admin/billing>。

本仓库于 2026-10-29 以完整工作区快照建立独立主线，包含已部署的生成、复刻、手机号登录、会员支付、退款及并发改动。旧开发历史另存于本机备份，不作为本仓库的父提交。海宇企业官网是另一个项目，不包含在这里。

## 功能与当前边界

- 商品主图、详情、套图、局部修改、模特换姿与一键复刻。
- 一键复刻默认通过 EveryInfra 采集 12 个商品来源，支持手淘分享文本、主图/详情图/SKU 图分类、当天缓存和异步任务恢复。
- 复刻方案与执行检查点、分屏裁切校验、已生成图片找回、部分成果保留和进度恢复。
- 手机号验证码注册／登录、密码管理、安全会话与工作区权限。
- 任务中心、素材库、历史记录和下载。
- 会员月付／年付、逐月积分、积分包、额度冻结／结算与流水。
- 支付宝官方证书模式付款、验签到账、订单核对和原路退款。
- 客服与财务后台：用户停用／恢复、人工积分调整、赠送会员、退款记录、统计和 CSV 导出，重要操作保留审计。

微信 Native 已按服务端主动查单方式开放。后台独立定时核对订单，持久保存查单时间、租约、重试次数及错误；用户关闭页面后继续补单。仅将微信返回的、验签通过且订单/商户/金额匹配的成功收款入账。微信退款同样通过已实现的服务端查询流程确认结果。

此模式不依赖 APIv3 回调解密密钥；已签名的加密通知只作提示，不直接发放积分。原海宇平台和商户配置不作修改。已完成真实接口下单、查单及关闭未付款测试订单；没有进行真实付款、到账或退款验收。

## 技术与目录

- 前端：React 19、TypeScript、Vinext / Vite；页面位于 `app/`，组件位于 `components/`，业务模块位于 `features/`。
- 后端：Python 3.12+、FastAPI、SQLAlchemy、Alembic；应用位于 `backend/app/`。
- 数据与任务：PostgreSQL、Redis、Celery、事务 outbox；素材使用 S3 兼容存储或本地开发存储。
- `backend/tests/`、`tests/`、`deploy/tests/`：后端、前端逻辑和部署检查。
- `deploy/`：部署源文件、预检和已执行的运维脚本。历史脚本不能作为任意环境的一键部署入口。
- `verification/`：此前测试与发布证据；历史记录不表示当前提交已重新跑过所有检查。
- `RELEASE-STATUS.txt`：当前部署及功能边界；`ACCOUNT-MEMBERSHIP-DELIVERY.txt`：账户会员功能说明。

代码中的 `POLARIS_*` 环境变量及部分内部包名沿用既有接口，不应为改名而修改生产配置或迁移标识。

## 本地开发

要求 Node.js >=22.13.0、Python >=3.12、Git。首次准备：

```powershell
npm ci
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
git config --local core.hooksPath .githooks
git config --local remote.pushDefault origin
git config --local push.default simple
```

本地编译仍保留 Vinext 的托管兼容入口。若缺少 `.openai/hosting.json`，可创建内容为 `{"d1":null,"r2":null}` 的本机占位文件；不要填写其他项目的资源绑定，该文件不入库。

开发机已有 `.venv-codex` 时可复用它，无需重建。真实 API、短信及支付凭据只保存在未跟踪的本地配置或服务器受限文件中。不要覆盖已有 `.env.local`、`.env.development.local` 或 `backend/.env`。

### 无真实支付的账户预览

在两个终端分别运行：

```powershell
.\.venv\Scripts\python.exe backend/scripts/run_membership_preview.py
```

```powershell
$env:NEXT_PUBLIC_AUTH_MODE = "development"
$env:VITE_API_PROXY_TARGET = "http://127.0.0.1:18939"
npm run dev -- --host 127.0.0.1 --port 18938 --strictPort
```

访问 `http://127.0.0.1:18938/account`。后端使用独立 SQLite、合成开发身份且关闭真实支付；这不是生产登录验收环境。如果现有 `.env.development.local` 或 `.env.local` 设置了 `VITE_API_PROXY_TARGET`，其值会优先于终端变量，应先核对是否指向该预览后端。

完整开发依赖可通过根目录 Compose 启动。对外部署必须使用独立密钥、正式数据库和生产身份模式，不能直接暴露本地开发配置。

## 并发与发布

目前生图使用 Redis 共享准入，上限为 3000；Celery 使用线程池和共享异步执行器，配置 3000 个任务槽位。复刻分析业务并发值为 0，表示不额外限制该业务，并不意味着服务器资源无限。

已完成 3000 个模拟任务的调度验证，没有执行 3000 张真实付费图片压测。真实吞吐仍受上游、图片大小、带宽、内存、数据库和存储影响。

当前生产后端镜像为 `huipin-integrated/backend:20261029-everyinfra`（继承并发修复），前端为 `huipin-integrated/frontend:20261029-everyinfra`，数据库迁移到 `20261029_0034`。部署使用现有基础 Compose 和会员支付叠加配置，必须保留三个工作队列、支付积分设置、共享并发与资源预算。禁止直接用旧基础配置覆盖当前生产服务。

GitHub 推送只保存源码，不自动发布生产。2026-10-29 已将微信主动补单与 EveryInfra 采集统一合并到 `main` 并发布；商户、队列和并发配置保留。详情见 [RELEASE-STATUS.txt](RELEASE-STATUS.txt) 与 `verification/everyinfra-integrated-*.json`。

## 验证

```powershell
npm run typecheck
npm run lint
npm run build
node --experimental-strip-types --test tests/replica-progress.test.mjs tests/replica-unknown-retry.test.mjs tests/auth-route-policy.test.mjs tests/phone-login-completion.test.mjs
.\.venv\Scripts\python.exe -m pytest backend/tests/test_provider_capacity.py backend/tests/test_task_runner_selection.py
.\.venv\Scripts\python.exe -m pytest backend/tests/test_credit_payments.py backend/tests/test_credit_memberships.py backend/tests/test_admin_billing.py
.\.venv\Scripts\python.exe -m unittest discover -s deploy/tests
```

完整测试还包含需要隔离 PostgreSQL 的并发与迁移场景，见 CI。请区分新增改动的定向检查、历史发布证据和整个仓库的检查结果。

## 仓库隔离

此项目只允许推送到 `cai18122668086/haiyu-huipin`。不得向参考项目、姐妹网站或旧远端推送代码、补丁、品牌、登录、支付或说明文档，也不得对它们创建 PR、合并或部署。

`AGENTS.md` 与 `.cursor/rules/repository-isolation.mdc` 固化开发约束；`.githooks/pre-push` 校验目标仓库，并阻止把旧历史分支推入新主线。新克隆需执行上面的 hooks 安装命令。Git 本地检查可以被人为绕过，不能代替账号权限控制。

远程 `origin` 应指向上述私有仓库；合并协作修改前先获取最新 `origin/main`。不要强推共享主线，不要把凭据、真实环境文件、数据库、用户素材或运行缓存加入版本控制。
