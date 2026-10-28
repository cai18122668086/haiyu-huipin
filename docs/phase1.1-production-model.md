# Phase 1.1 生产数据模型

本模型是后续局部修改、商品详情图、前端数据层和生产安全工作的唯一
数据基线。

```text
users ──< workspace_members >── workspaces
workspaces ──< assets
workspaces ──< jobs ──< job_items ──< job_attempts ──< usage_events
                    └──< job_assets >── assets
                    └──< job_plans
                    └──< outbox_events
                    └── credit_reservations ──< credit_ledger_entries
workspaces ── workspace_credit_accounts
workspaces ── workspace_limits
```

## 核心不变量

1. 任何资产、任务和条目关系都不能跨 workspace。
2. 任务输入和产物必须通过 `job_assets` 表达，不能藏在 JSON ID 数组中。
3. 局部修改统一保存 source、selection_mask、normalized_source、api_mask、
   raw_model_output 和 locked_delivery。
4. 详情图每页对应一个 job_item；计划、尝试和 usage 分别持久化。
5. 任务创建、额度预留和 outbox 事件处于同一个 PostgreSQL 事务。
6. Worker 重投只能复用或识别已有 attempt，不能重复产生付费调用。
7. `outcome_unknown` 不进行无条件自动付费重试。
8. provider usage 逐请求追加写；job 上的费用只是可重建汇总。
9. `actual_cost_micros=NULL` 表示未知，不能用零替代。
10. Redis 全丢后仍能从 PostgreSQL outbox 恢复任务派发。

完整列、约束和索引以 `backend/app/models.py` 与 Alembic 迁移为准。
