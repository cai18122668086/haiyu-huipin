# 海宇绘品后端

FastAPI、任务执行、复刻、账户会员及支付服务。当前已接入图片生成服务；完整项目入口和最新状态以根目录 README.md 与 RELEASE-STATUS.txt 为准。

## 已实现

- FastAPI 应用与统一错误结构。
- PostgreSQL 持久化与 Alembic 数据库迁移。
- PNG、JPEG、WebP 上传校验与 S3 兼容对象存储。
- 用户、工作区成员和服务端租户边界。
- 关系化 job、job_item、job_asset、attempt、plan 和 usage 模型。
- API v1、幂等写入、事务 outbox、额度预留与精确成本。
- 私有对象存储与短期访问 URL。
- Redis + Celery Worker 和独立 outbox dispatcher。
- 详情图计划确认门槛与零付费模拟执行器。

## 完整环境

从仓库根目录启动：

```bash
docker compose up -d --build
```

访问：

- API：`http://localhost:8000`
- OpenAPI：`http://localhost:8000/docs`
- 存活检查：`http://localhost:8000/health/live`
- 就绪检查：`http://localhost:8000/health/ready`
- MinIO 控制台：`http://localhost:9001`

开发默认密码只允许用于本机 Compose；对外环境必须替换。

## 快速单元测试

单元测试使用 SQLite 和本地临时存储，不依赖 Docker：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m mypy --config-file backend\pyproject.toml backend\app
.\.venv\Scripts\python.exe -m pytest backend
```

开发身份默认工作区为 `ws_development`。该模式只能用于本地开发；
`POLARIS_ENVIRONMENT=production` 时会拒绝 development 身份和开发密钥。

## 测试与生产边界

- 模拟执行器只生成任务状态与占位输出元数据，不调用真实生图服务。
- 正式服务已包含手机号登录、真实生成与支付宝官方支付，不能以模拟测试替代真实渠道验收。
- 微信正式通道仍需有效商户配置和验收；真实密钥不属于源码。
- 3000 任务槽位与 Redis 生图共享额度已经实现，但实际负载能力仍需按生产资源验证。

## 海宇本地服务配置

运营方在被 Git 忽略的 `backend/.env` 中配置密钥，前端无需填写 API Key。
`POLARIS_ANALYSIS_*` 控制规划模型，`POLARIS_TENGLI_VISION_MODEL` 控制视觉审核，
`POLARIS_IMAGE_MODEL` 和 JSON 数组 `POLARIS_IMAGE_FALLBACK_MODELS` 控制生图顺序。
默认关闭浏览器配置接口；生产环境始终不可用。

海宇网关使用 `https://api.haiyushuke.com`，适配器追加 `/v1`，处理异步任务、
幂等查询和图片链接下载。多张参考图会合并为带编号的参考板，保留全部输入；
局部改图另附选区示意，原有交付蒙版合成和视觉审核继续执行。
结果不明时停止切换，保留原任务查询，避免重复生成。

目前网关只验收默认画布。尺寸参数支持修复后，把
`POLARIS_HAIYU_IMAGE_SIZE_ENABLED` 设为 `true` 并重启 API 和 worker，
再验证套图的精确尺寸。原有尺寸校验保持启用。
仅在本机代理使用 fake-IP DNS 时，才需要把已核实的图片 CDN 域名放入
`POLARIS_IMAGE_RESULT_ALLOWED_HOSTS`；内网和回环图片地址仍被拒绝。
本地预览固定使用 `same_origin` 素材链接，案例原图和签名素材都通过前端
`/api` 代理读取，避免缩略图可见、复制原图却被跨域规则拦截。

`python backend/scripts/provision_inspiration_catalog.py` 可重复补齐原始 18 个灵感条目：
15 组有原始图片的案例，以及 3 个保留原文的提示词模板。模板不导入历史模特库
占位封面；用户上传自己的商品图后进入商品提示词流程。已有案例正文和素材关联保持不变。

保持现有本地预览数据库和素材目录启动：

```powershell
python backend/scripts/run_haiyu_preview.py
```
