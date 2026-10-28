# API v1 生产契约

更新日期：2026-07-25

本文件是 Phase 1.1 之后唯一有效的业务 API 契约。机器可读契约位于
`docs/openapi-v1.json`，前端生成类型位于
`lib/api/generated/openapi.d.ts`。

## 边界

- 业务资源只通过 `/api/v1` 暴露。
- `/health/live` 和 `/health/ready` 不版本化。
- 当前执行器仍是零付费调用的生产形态模拟器。
- 前端页面尚未切换到 API v1。
- `workspace_id` 位于路径中，但服务端仍必须从 Principal 和
  `workspace_members` 验证访问权。
- 生产环境禁止启用 development 身份模式。

## 资源路径

```text
GET  /api/v1/me
GET  /api/v1/workspaces
GET  /api/v1/workspaces/{workspace_id}

POST /api/v1/workspaces/{workspace_id}/assets
GET  /api/v1/workspaces/{workspace_id}/assets
GET  /api/v1/workspaces/{workspace_id}/assets/{asset_id}
POST /api/v1/workspaces/{workspace_id}/assets/{asset_id}/access-url

POST  /api/v1/workspaces/{workspace_id}/jobs
GET   /api/v1/workspaces/{workspace_id}/jobs
GET   /api/v1/workspaces/{workspace_id}/jobs/{job_id}
PATCH /api/v1/workspaces/{workspace_id}/jobs/{job_id}
POST  /api/v1/workspaces/{workspace_id}/jobs/{job_id}/submit
POST  /api/v1/workspaces/{workspace_id}/jobs/{job_id}/cancel
POST  /api/v1/workspaces/{workspace_id}/jobs/{job_id}/retry
POST  /api/v1/workspaces/{workspace_id}/jobs/{job_id}/clone
PUT   /api/v1/workspaces/{workspace_id}/jobs/{job_id}/plan
POST  /api/v1/workspaces/{workspace_id}/jobs/{job_id}/plan/confirm
```

## 幂等

资产上传、任务创建、提交、取消、重试、复制和计划确认必须提供
`Idempotency-Key`。记录保留七天。

- 同 key、同请求：返回第一次创建的资源。
- 同 key、不同请求：`409 IDEMPOTENCY_KEY_REUSED`。
- 同 key、首次请求尚未完成：`409 IDEMPOTENCY_IN_PROGRESS`。

## 任务状态

```text
draft
queued
planning
awaiting_confirmation
processing
cancelling
partial
completed
failed
cancelled
```

`partial/completed/failed/cancelled` 是不可变终态。重试创建新任务。
详情图计划必须通过结构校验并显式确认，才会进入 processing。

## 成本

- 数据库存储 `bigint` 微单位。
- API 使用十进制字符串返回 `micros`，避免 JavaScript 精度损失。
- `actual=null` 表示未知；零只表示已确认没有发生费用。
- 客户端不能提交估算、实际成本、提示词版本或策略版本。
- 提交任务时预留额度；终态时结算并释放余额。

## 资产安全

- API 不返回 provider、bucket、object key 或旧 `storage_key`。
- 只有 `ready` 资产可参与任务。
- 下载使用经授权生成的十分钟短期 URL。
- 局部修改只有验证通过的 `locked_delivery` 可以标记为可交付。

## 分页

资产和任务列表使用不透明 keyset cursor：

```json
{
  "items": [],
  "next_cursor": null,
  "has_more": false,
  "total": null
}
```

只有 `include_total=true` 时计算 total。游标绑定原筛选条件。

## 错误

```json
{
  "error": {
    "code": "ASSET_NOT_FOUND",
    "message": "未找到指定素材。",
    "details": null,
    "request_id": "req_xxx"
  }
}
```

跨工作区资源查询统一返回 `404`，不泄露资源是否存在。
