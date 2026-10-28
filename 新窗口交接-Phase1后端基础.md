# 新窗口交接：Phase 1 后端基础

更新日期：2026-07-25

## 当前状态

Phase 0 前端设计系统、工具注册表和平台数据结构保持不变。Phase 1 第一批后端
基础已经建立，前端暂未切换到真实接口。

本批实现：

- FastAPI 与统一 JSON 错误结构。
- PostgreSQL 18 + SQLAlchemy + Alembic。
- Redis 8 + Celery 5 异步任务队列。
- S3 兼容存储抽象；本地 Compose 使用 MinIO。
- 图片类型、字节数和像素数校验。
- 资产上传、列表与详情接口。
- 任务创建、列表、详情、取消与重试接口。
- `local_edit` 与 `detail_pack` 统一任务模型。
- 队列失败落库、任务状态转换、重试关系、usage 与成本字段。
- SQLite/本地存储单元测试模式。

## 重要边界

- 当前 Worker 只模拟任务状态流和占位输出，不调用任何付费图片 API。
- 不包含真实登录、支付、额度扣减或部署。
- Compose 密码只供本机开发，不能用于测试或生产环境。
- 前端仍使用 `lib/mock-data.ts` 和页面内状态。
- 后续接局部修改时必须保留选区外像素零变化的严格锁定规则。
- 后续接商品详情图时必须保留规划校验、逐页重试、断点续跑和 usage 记录。

## 本地启动

Docker 位于 WSL Ubuntu-24.04。如 Windows PATH 中找不到 Docker，可执行：

```powershell
wsl.exe -d Ubuntu-24.04 -e sh -lc "cd '/mnt/c/Users/Administrator/Documents/初步网站建立' && docker compose up -d --build"
```

服务：

- API：`http://localhost:8000`
- OpenAPI：`http://localhost:8000/docs`
- MinIO API：`http://localhost:9000`
- MinIO 控制台：`http://localhost:9001`
- PostgreSQL：`localhost:5432`
- Redis：`localhost:6379`

## 关键文件

- `compose.yaml`
- `backend/Dockerfile`
- `backend/app/main.py`
- `backend/app/routes.py`
- `backend/app/models.py`
- `backend/app/storage.py`
- `backend/app/task_runner.py`
- `backend/app/tasks.py`
- `backend/migrations/versions/20260725_0001_phase1_foundation.py`
- `docs/phase1-api-contract.md`

## 下一批建议

1. 增加后端 readiness、结构化日志和 CI。
2. 前端建立单一 API Client 与服务端状态缓存。
3. 先把素材库和生成记录切换到真实 API。
4. 再接两个工作台的真实上传和任务轮询。
5. 完成上述基础后进入 Phase 2，抽取局部修改服务层。

仍不应直接开始真实登录、支付或生产部署，除非用户明确授权。
