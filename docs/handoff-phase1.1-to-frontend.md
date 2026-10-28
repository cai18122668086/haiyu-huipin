# Phase 1.1 → 前端改版交接

## 基线

- Phase 1.1 基线标签：`phase-1.1-baseline`
- Phase 1.1 开发分支：`codex/phase1-backend-foundation`
- 前端改版分支：`codex/frontend-ui-refresh`
- API 版本：`/api/v1`
- Alembic 迁移头：`20260725_0004`
- 后端版本：`1.1.0`

Phase 1.1 已完成生产数据模型、workspace 权限边界、资产与任务 API、
幂等、额度预留、transactional outbox、worker 防重、usage 账本、私有对象存储、
OpenAPI 快照、前端生成类型和 CI 基础。

当前执行器只模拟任务状态，不调用真实图片生成服务，也不会产生付费 AI 调用。
生产认证与真实 provider 仍保持 fail-closed。

## 前端当前状态

页面目前仍主要使用 `lib/mock-data.ts` 和页面内状态，尚未整体切换到 API v1。
已经存在：

- `lib/api/client.ts`：OpenAPI 类型化客户端。
- `lib/api/generated/openapi.d.ts`：由 OpenAPI 生成的 TypeScript 类型。
- `lib/api/api-error.ts`、`lib/api/error-mapper.ts`：统一错误处理基础。
- `lib/query/query-client.ts`、`app/providers.tsx`：TanStack Query 基础。
- `features/assets/api/assets-keys.ts`、`features/jobs/api/jobs-keys.ts`：
  query key 工厂。

因此前端改版可以先稳定布局、组件和交互，再进行真实数据接入。

## 建议的新对话目标

在不修改 API v1 语义和生产数据模型的前提下，重新设计并实现前端界面：

1. 盘点并保存当前核心页面的视觉基线。
2. 确定全局设计语言、导航结构、响应式断点和组件规范。
3. 优先改造应用壳、首页、Dashboard、素材库、任务列表。
4. 再改造局部修改与商品详情图两个工作台。
5. 为加载、空数据、错误、处理中、部分成功、完成和取消状态提供一致表现。
6. 界面确认后，再逐页接入 API v1；不要重新引入第二套请求模型。

如果界面需要新增字段、动作或任务状态，应先对照
`docs/phase1-api-contract.md`。纯视觉与布局修改不需要等待后续阶段。

## 不应破坏的边界

- 所有后端业务接口保持 `/api/v1/workspaces/{workspace_id}/...`。
- 前端不得直接持有对象存储 key，只使用短时签名 URL。
- 创建资产和任务等写操作必须携带 `Idempotency-Key`。
- 金额使用字符串形式的整数 micros，额度使用整数 credits。
- 任务终态包括 `completed`、`partial`、`failed`、`cancelled`。
- 商品详情图必须经过计划确认后才进入生成阶段。
- 不要把任务输入、输出资产 ID 重新塞入 JSON 数组作为唯一关系来源。

## 关键资料

- `docs/phase1.1-production-model.md`
- `docs/phase1-api-contract.md`
- `docs/openapi-v1.json`
- `backend/app/routes_v1.py`
- `backend/app/schemas_v1.py`
- `backend/app/models.py`
- `lib/api/generated/openapi.d.ts`

## 验证命令

```powershell
npm run lint
npm run typecheck
npm test

.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m mypy --config-file backend\pyproject.toml backend\app
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

本地基础设施：

```powershell
wsl.exe -d Ubuntu-24.04 -e sh -lc `
  'cd /mnt/c/Users/Administrator/Documents/初步网站建立 && docker compose up -d --build'
```

API readiness：`http://127.0.0.1:8000/health/ready`

## 已验证结果

- 后端 9 个测试通过。
- Ruff 与 Mypy 通过。
- 前端 lint、TypeScript、生产构建和 2 个渲染测试通过。
- 空 PostgreSQL 数据库可从零迁移至 `20260725_0004`。
- Compose API、PostgreSQL、Redis、MinIO、worker、dispatcher 正常运行。
- 资产上传 → outbox → worker → 任务完成的端到端冒烟通过。
