# Polaris 全平台 Skill Parity Matrix（2026-10-06）

这份矩阵记录所有纳入本轮范围的生图入口，按原始 Skill 的输入角色、提示词协议、生成方式、复核、重试和交付契约验收。商品复刻和真实商品爬虫是独立工具链，按用户要求排除，不计入本轮覆盖率。

## 判定口径

- **路由 / job**：前端入口和 `JobType` 一致，并由 `create_job` 建立持久化 job。
- **source / hash**：manifest 绑定 source，provider 在执行前重新校验 Skill source。
- **planner / generator**：角色化参考束、原始 Skill 约束和真实生成调用均进入 provider 请求。
- **review / retry / delivery**：复核结果、失败边界、用户可见出口与交付状态必须可追踪。
- **代码验收**：定向回归、契约测试、类型检查和 diff 检查通过；真实外部网关任务仍需在有可用素材和配额时补跑。

## 生图入口

国内详情和跨境详情在这里拆成两行，便于分别记录尺寸、语言和平台契约；因此表中有 16 行生图模式，对应 15 个用户入口。

| 入口 | route / job | skill source / manifest | planner / generator | review / retry / delivery | 当前验收 |
|---|---|---|---|---|---|
| 一句话改图 | `/workspace/local-edit` → `local_edit` | `masked-image-repair`；manifest `2578e926…` | `MaskedRepairProvider`；原图、显式 selection mask 和 authority 区域角色固定 | 显式 source + mask multipart；`quality=auto` 与 `execution_path=auto/deterministic/hybrid/cropped/full`；确定性平面色填充含糊时 fail-closed；raw 保存、视觉复审和严格像素锁定 | 代码验收 >=90%；17 个 masked/gateway 回归通过；待真实网关任务补验收 |
| 商品换色 | `/workspace/product-image/change-color` → `product_image` | `xinghe-wanneng-shengtu-3-0`；manifest `ae240178…` | `ProductImageProvider`；product/reference 角色和 preset recipe | Product Consistency Lock、渐进参考角色、平台/任务 recipe、合规负约束；技术 QA；瞬态网关/输出失败最多一次重试；结果页人工 checklist | 代码验收 >=90%；provider、前端、契约回归通过；待真实任务 |
| 更换背景 | `/workspace/product-image/change-background` → `product_image` | 同上 | 同上 | 同上 | 代码验收 >=90%；待真实任务 |
| 更换场景 | `/workspace/product-image/change-scene` → `product_image` | 同上 | 同上 | 同上 | 代码验收 >=90%；待真实任务 |
| 更换模特 | `/workspace/product-image/change-model` → `product_image` | 同上 | 同上 | 同上 | 代码验收 >=90%；待真实任务 |
| 场景主图 | `/workspace/product-image/scene-main` → `product_image` | 同上 | 同上 | 同上 | 代码验收 >=90%；待真实任务 |
| 电商白底图 | `/workspace/product-image/white-background` → `product_image` | 同上 | 同上 | 同上 | 代码验收 >=90%；待真实任务 |
| 模特商品图 | `/workspace/product-image/model-image` → `product_image` | 同上 | 同上 | 同上 | 代码验收 >=90%；待真实任务 |
| 商品试穿 | `/workspace/product-image/try-on` → `product_image` | 同上 | 同上 | 同上 | 代码验收 >=90%；待真实任务 |
| 一键模仿（独立工作台） | `/workspace/reference-remix` → `reference_remix` | 默认 `xinghe-reference-image-remix-3`；exact 路由显式使用 `reference-visual-reverse-remix`，manifest `c17306e0…` | 产品图是 identity authority；参考图只提供 visual strategy；staged analysis → 原创 remix prompt → image edit | identity lock、role map、commercial intent、forbidden transfer、post-generation review；结果页人工 checklist；只允许用户明确发起的有界修复，不做无限自动重绘 | 代码验收 >=90%；exact route/字段/前端回归通过；待真实素材验收 |
| 模特换姿势 | `/workspace/model-pose-transfer` → `model_pose_transfer` | `ecommerce-model-pose-transfer`；manifest `c82c0083…` | `ModelPoseTransferProvider`；model source first/only identity authority，pose reference second/guidance only | 单次多图编辑、一个输出；Skill 明确禁止 OpenPose、自动姿势评分、自动接受、自动修正和自动重试；结果页结构化人工 checklist | 代码验收 >=90%；单图/provider/前端回归通过；待真实素材验收 |
| 电商创意图 | `/workspace/creative-image` → `creative_image` | `xinghe-ecommerce-creative-image`；manifest `fbbe3486…` | `CreativeImageProvider`；普通/灵感两路，产品与竞品参考角色分离 | CTR-first、产品保真、平台/语言、user instruction、commercial intent、forbidden transfer、post-generation review；技术完整性 + 人工 checklist；不做无限语义重绘 | 代码验收 >=90%；字段语义、prompt、前端回归通过；待真实 helper/gateway 任务验收 |
| 商品详情图（国内） | `/workspace/detail-pack` → `detail_pack`，`market_region=domestic` | `aurora-domestic-ecommerce-detail-page` v4.3.0；manifest `f447ebf8…`；source 来自用户提供的 `电商生图skills_5个_2026-09-23.zip` | `DomesticDetailProvider` + Aurora compact-plan validator；8/10/12 屏，连续相邻双屏 `PAIR_PLAN_MERGE` | 每 pair 一次 3:8 画布，确定性切成两张 3:4；reference edit 并发=1；完整 group→screen approval、`audit-pair` 结构像素绑定、最多一次整 pair repair、最终用户复核；未通过真实语义审计前不宣称完成 | 代码契约已接入；本地 pair/split/manifest 测试通过；bundled regression 仍需在含 `jsonschema` 的运行时执行，真实网关任务待用户测试；生产未切换 |
| 商品详情图（跨境） | `/workspace/detail-pack` → `detail_pack`，`market_region=international` | `xinghe-crossborder-detail-3`；manifest `9677eb36…` | 平台-aware `CrossborderDetailProvider`；generic/亚马逊 16:9 2K 默认，Ozon/WB 900x1200 3:4，支持显式 `WIDTHxHEIGHT` | language/platform/size 贯通 schema → planner → generator；独立模块、统一尺寸、PNG 交付契约；单页失败可继续，其它成功页不自动重绘 | 代码验收 >=90%；跨境尺寸、routing、provider 回归通过；待真实任务 |
| 亚马逊套图 | `/workspace/amazon-visual-suite` → `amazon_visual_suite` | `xinghe-amazon-visual-suite`；manifest `c5ca76ea…` | `AmazonVisualSuiteProvider`；21 canonical keys、角色、尺寸和 item identity 固定 | 生产默认 QA 开启，最多 2 轮；首轮只把失败项置回 PENDING 并隔离旧交付物，重绘 prompt 带 retry guidance；最终失败阻断完成/进入 partial；前端区分技术完成与业务质量 | 代码验收 >=90%；Amazon provider/QA/detail/前端回归通过；待真实 21/21 任务 |
| 国内电商主图（最高档） | `/workspace/domestic-main-image` → `domestic_main_image` | 生产 `aurora-domestic-ecommerce-main-image-v20`；source `6cc143db…`，`SKILL.md` `a6ce9864…` | `AuroraV20HarnessRunner` + `sequential_v20`；逐页生成、审计、返修、set audit | `AWAITING_USER_FINAL_REVIEW` → `finalize --approval-text`；瞬态失败有界重试；最终复核由用户确认 | 既有真实 6/6 任务完成并等待最终复核；本轮候选调度改动保持隔离，未混入其它入口 |

## 明确排除范围

| 工具 | 处理方式 |
|---|---|
| 商品复刻 | 按用户要求不接入本轮 Skill parity，不修改其 URL/session/资源抓取链路。 |
| 真实商品爬虫 | 按用户要求不接入本轮 Skill parity，不修改 `crawl_tasks`、local worker 或会话链路。 |

## 代码证据索引

- 入口注册与 skill/job 映射：`lib/feature-registry.ts`、`backend/app/skill_runtime.py`。
- 单图适配器与角色化资产：`backend/app/new_image_skill_provider.py`、`product_image_provider.py`、`reference_remix_provider.py`、`creative_image_provider.py`、`model_pose_transfer_provider.py`。
- 路由与持久化 job：`backend/app/routes_v1.py` 的 `create_job`、`_new_job_from_payload`、`_add_items_and_inputs`。
- 统一真实 provider 注册：`backend/app/execution.py` 的 `single_image_delegate`、`new_routes`、Amazon quality service。
- 详情交付契约：`backend/app/new_detail_skill_provider.py`、`domestic_detail_provider.py`、`crossborder_detail_provider.py`。
- manifest/source 校验：`backend/app/skill_runtime.py`、`backend/scripts/verify_skill_manifests.py`。
- 定向回归：`backend/tests/test_new_image_skill_providers.py`、`backend/tests/test_xinghe_single_image_provider.py`、`backend/tests/test_new_detail_and_amazon_providers.py`、`backend/tests/test_amazon_visual_quality.py`、`backend/tests/test_masked_repair.py`、`tests/new-skill-workspaces.test.mjs`。

## 本轮已落地变更

1. `local_edit / masked-image-repair`：显式 source + mask multipart、quality/execution path、确定性填充 fail-closed、raw/visual review guidance 和前端选项。
2. `product_image` 八个入口：统一 Skill canonical prompt、Product Consistency Lock、渐进参考角色、平台/任务 recipe、技术 QA、一次保守重试和人工 checklist。
3. `reference_remix`：独立工作台显式选择 `reference-visual-reverse-remix`，固定产品优先，传入 identity/strategy/commercial/forbidden/review 字段和人工 checklist。
4. `model_pose_transfer`：保留原 Skill 的一次生成、无自动 pose scoring/retry 约束，补结构化人工审核 checklist。
5. `creative_image`：`instruction`/`user_instruction` 语义统一，补 CTR/product/compliance/review prompt 契约和人工 checklist。
6. 国内/跨境详情：补 planner 计划契约、默认尺寸/语言、显式尺寸解析、模块输出格式和具体 artifact 尺寸/格式校验。
7. Amazon：生产默认两轮 quality review，失败项有界重绘并隔离旧产物，最终 QA 失败阻断可交付状态。
8. 前端：单图、商品图、创意图、exact reference 和 model pose 在结果出口呈现 Skill 对应人工检查清单；商品图保留服务端授权的失败项重试入口。

## 验收结论

此前的整体 90% 结论不适用于本次更新后的国内详情 Skill。Aurora v4.3 已完成 source/manifest、8/10/12、3:8 pair→3:4 slice、审批指纹、串行 pair 生成、对象存储可恢复画布和结构像素绑定接入；完整 harness 语义审计与真实网关任务仍需在本地候选环境中验收。商品复刻与真实商品爬虫仍按用户要求排除；生产未切换。


## 2026-10-06 用户更新 Skill 后的国内详情切换

本轮改用用户压缩包内的 `aurora-domestic-ecommerce-detail-page` v4.3.0，保留旧 Xinghe source 作为历史回滚。国内详情创建只接受 8/10/12 屏，默认使用未完成后台核验标记的 `INTERNAL_REVIEW_1440_3_4` profile（单屏 1440×1920、pair 1440×3840）；真实运行前需完成平台规格核验。每两个相邻屏幕只发一个参考图编辑请求，生成 3:8 pair canvas 后固定切片，并把非交付原画布写入对象存储供第二 worker 恢复；provider edits 并发锁为 1。计划必须带 `campaign-approval.md` 内容 SHA、屏数和 3:8 比例，用户确认后才生成；结构像素审计标记为 semantic review required，最终交付仍需用户复核。

本地候选验证：`test_aurora_domestic_detail_skill.py` 7 项通过，source manifest/hash 回归通过，TypeScript 与 compileall 通过。由于当前宿主 Python 缺少 `jsonschema`，Aurora bundled regression 和真实 planner 运行必须在重建的依赖镜像中执行；该阻塞解除并完成用户详情图测试前，不宣称国内详情已达到 90% 等价，也不部署生产。
