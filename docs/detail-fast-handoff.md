# 国内详情图·快速：实现与本地验收

发布更新：2026-10-30已随PR #1统一合并部署，实际状态与备份见[统一发布记录](release-suite-fast-20261030.md)。下文保留开发验收时的历史记录。

分支：`codex/detail-fast-20261029`。只面向私有仓库 `cai18122668086/haiyu-huipin`，独立工作区 `huipin-detail-fast-20261029`。本分支不部署生产、不修改服务器 Compose，也不推送 main。

已吸收整合窗口合入主线的 `3f01230` 原版规划/原图修复和 `b6b91b8` 发布记录；当前生产基线为 consolidated v2，本分支没有切换生产。没有复制主工作区脏内容，原版尺寸辅助模块 `native_portrait_delivery.py` 原样保留。

## 范围与实际瓶颈

国内精致详情原版已经是一次合并规划，成图也没有常规 AI 审核或自动审美重绘。本次没有将“三轮规划改一轮”。优化的是参考资料输入、逐页重复输出全套共享约束、每批内按序号三秒错峰，以及 `gather` 造成的整批等待。

国内详情新增明确的“原版 / 快速”，默认原版。快速显式请求 `detail_skill: refined`、`detail_speed: fast`，绑定独立 pipeline `xinghe-domestic-detail-v4.0-fast` 和 Skill `xinghe-ecommerce-detail-domestic-4-fast`。原版标准值不进入请求序列化，所以旧请求幂等身份和旧任务设置保持不变。历史任务按服务端 pipeline 恢复，草稿创建后不能改版本。历史 `quick` 双屏模式不迁移。

## 独立实现

- `backend/app/detail_fast.py`：一次完整共享策略和逐页创意差异，模型输出 `page_prompt`，服务器组合共享身份、已确认事实、风格、物理关系和该页文案/构图。完整提示词仍逐页持久保存。完整 1–12 页、独立 3:4 画布、需求/模块绑定、构图多样性继续校验，缺页或共享锁/提示词不一致直接失败。主身份来源必须是实际 product_reference，不将风格参考当商品事实。
- `backend/app/detail_fast_runtime.py`：集中选择快速和原版的契约，原版 `refined_detail.py` 不作快速改写。
- `backend/app/detail_fast_gateway.py`：只用于快速版的单次提交网关。生图前持久写回执；绑定归属、源图 SHA、完整提示词、整套冻结签名、Skill 内容哈希、模型、画质和尺寸。成功原始字节先保留，再经公共执行器发布。交付存储失败后，正常失败项重试可零生图调用复用原图。提交中、下载结果未知、取消或回执写入失败均阻止盲目重发。仅新的失败项重试、绑定一致且真实 HaiyuTransport 中存在唯一 accepted 回执时，可携带原幂等键进入 `reconcile_only`，只 GET 原任务和原文件，绝不 POST 新图。查询失败仍保留 unknown，下一次不会退化为重新生图。选中页明确修订则是独立的新生成授权。
- 快速套图复用已有有界滚动调度和数据库租约计数，单套上限 20（当前详情最多 12 页），尊重更小 worker 上限；任一页完成即补位，没有三秒错峰。共享全站图像准入继续生效。
- 完整复制原 Skill 的 22 个文件；只替换副本 SKILL.md，原包其他 21 个文件字节一致。独立 manifest 与生成脚本不重写原锁。

规划每次必读资料由原版 21 文件、86,766 UTF-8 字节，缩为快速 6 文件、26,159 字节，减少约 69.85%。该数值是资料字节数，不是实际 tokens 或整套耗时提升率。必要文件是快速 SKILL、买家需求、页面规划、设计强度、商品一致/物理关系、合规事实约束；其余完整资料继续留在副本中。

生图模型和 `high` 画质、请求 1440×1920 沿用原版；独立保存原图，不本地放大、拼接或覆盖文字。程序完整性通过标记 `STRUCTURALLY_VALIDATED`，没有语义审核的结果为 `NOT_REVIEWED`，不伪装视觉 PASS。积分预留、结算、归属、签名下载和 ZIP 走现有网站业务链路。

快速 profile 加入主线 `_BOUNDED_JSON_PLANNER_PROFILES`，直接继承 DeepSeek 的 32768 总输出预算、原推理配置、json_object 兼容与 `finish_reason=length` 收口；通用配置模型保持 24000 和 json_schema。没有另写原版截断策略或修改独立尺寸辅助函数。FastDetailProvider 和通用执行器同时复用原版 native portrait 规则，原始 1086×1448 已完整交付。

## 共享文件与冲突点

共享改动包括：

- `xinghe_agent.py`：独立 Skill/profile 选择、必要资料加载、策略规范化和完整性校验、提示词编译、审批绑定、原图保留与 mock 契约；原版函数继续由原契约处理。
- `execution.py`：快速 planner/provider 注册与滚动调度分支、数据库共享并发上限；保留原双屏依赖和原版错峰。
- `routes_v1.py`：创建 pipeline、草稿版本不可变、独立页确认/重试与预算分支。
- `schemas_v1.py`：新增可选 `detail_speed`，标准默认值在序列化时移除；跨境和历史双屏模式拒绝 fast。
- `provider_registry.py`、`skill_runtime.py`：独立路由和 Skill 白名单。
- `domestic_detail/auto_start.py`：快速有效完整方案沿用已授权自动开始路径。
- `services.py`：公共任务配置不暴露 `_detail_fast_invocations` 内的私有回执和存储路径。
- `app/workspace/detail-pack/page.tsx`、`lib/detail-skills.ts`、`lib/detail-speed.ts`：选项、默认、请求身份和历史恢复；OpenAPI 与生成 TypeScript 同步。

主图/亚马逊并行分支可能同时修改 `execution.py`、`xinghe_agent.py`、`schemas_v1.py`、`provider_registry.py`、`skill_runtime.py`、`services.py` 和生成类型。应逐个合并独立分支的最小接线，不能整文件覆盖。

## 验证状态

最终单次合并回归 **352 项全部通过**（120.50 秒），其中快速专项为 33 项契约、30 项持久网关、22 项真实路由合成输出、9 项规划传输，共 94 项；其余覆盖原版详情、执行可靠性、复刻、Skill runtime、任务路由和原版截断。前端 7 项详情选项/历史兼容测试通过。脱敏证据见 `verification/detail-fast-20261029/`。

已覆盖：1/6/12 页单次完整规划、6 画布同步提交、慢首图不阻塞第 3 页补位、两个 worker 共享更小并发上限、原图签名和 ZIP 字节、失败页重试、成功页复用、交付失败零调用恢复、选中页明确重新授权、自动开始、Skill/整套签名变更阻断、未知结果和取消防重、旧默认幂等、版本不可变、私有回执隔离。合成像素测试只验证技术流程，不证明真实视觉质量。

TypeScript、生产构建、定向 ESLint、后端 Ruff、10 个改动模块 Mypy 和 diff 检查已通过。真实接口脚本、数据库、凭据读取和图片仅位于忽略的 `backend/.data/detail-fast-real/` 与 `backend/.data/detail-fast-real-clean/`，不进入 Git；只读使用用户现存 3 张 OLAY 商品图和卖点，不回写原素材，不连接生产数据库。

## 真实链路验收

主验收样本使用独立 SQLite、独立存储、开发身份和本地合成积分，支付/SMS 关闭。通过上传、创建、确认、执行、失败项重试、逐张签名下载和 ZIP 路由；真实上游模型为规划 `deepseek-v4.1-flash-1017`、生图 `image-2.5-flare`，画质 high。

主样本 1 次真实规划，124.28 秒；输入 9814 tokens，输出 20857（包含 14604 reasoning tokens），总计 30671。供应商正文为 25771 UTF-8 字节，解析后的策略 JSON 再序列化为 26443 字节；6 个服务器组合后的完整生图提示词分别为 9480 / 9205 / 9261 / 9110 / 9336 / 9291 字节，全部冻结保存。6 次真实生图在 0.125 秒内开始，首张原图于上传后 181.09 秒返回，全部原图于 373.16 秒返回，均为 1086×1448 原始 PNG。

实测发现并修复了 Windows MAX_PATH 问题：旧私有键包含 workspace/job/item/SHA，实际路径超过本机限制，6 张均被正确记为 unknown，没有盲目再发。现在键为 `workspace/detail-fast/job/page-NNN-fullSHA.png`，完整 SHA 与 job/item/ordinal/方案归属仍严格核验，实际 Windows 工作区下 LocalImageStorage 保存/复用测试已通过。

修复后使用正常 failed retry API，以真实上游 accepted 回执恢复上述 6 张原图。验收网关禁止所有新规划和底层生图 POST，允许真实原任务 GET 与 CDN 下载；实际为 6 次任务 GET、6 次 CDN GET、新规划 0、新生图 POST 0。恢复后首张可下载为 30.63 秒，整套恢复/交付为 82.02 秒；6 个签名下载 HTTP 200、ZIP HTTP 200 且恰好 6 张，所有下载与首次返回原图 SHA 完全一致。原失败 job/items/attempts 全部保留，原任务冻结 90 积分结算 0，恢复任务结算 90，最终没有剩余冻结；未执行真实付款。

从主样本创建到恢复任务 completed 的墙钟时间为 548.99 秒，包含定位/修复间隔。不能把该数值当作无故障连续运行耗时或稳定 SLA；本轮完整通过的是正常原任务恢复后的真实网站交付链路，并未伪称首试 completed。上游未报告实际费用，整体 `actual_provider_cost=null`，不把内部积分或 USD 估算冒充实际人民币价格。

此前调试样本还实际调用过 1 次规划和 6 次生图：首次完整方案被放在 `type: json_object / properties` 的实例外壳中，原校验失败且零生图。只允许外层恰有 type/properties、type 为 object/json_object、内部恰有 analysis/plan 的严格归位已补齐；真 schema、缺页、混合/多余外层字段和 length 截断仍拒绝。该样本通过两次明确标记的本地完整回执重放继续调试，没有新规划请求，6 张真实原图仍保存在忽略目录，没有计入无中断端到端成绩。

本轮全部本地真实请求总计：2 次规划、12 次付费生图提交；恢复额外仅 6 次原任务 GET 和 6 次 CDN GET。另有 2 次零网络的本地规划回执重放。没有自动审美重绘、没有降低生图模型或 high 画质。脱敏数据见 `verification/detail-fast-20261029/real-summary.json`；完整回执、商品文案、提示词、私有任务 IDs、原图和 env 均不入库。

没有运行原版/快速同输入盲评，也没有用冻结同一完整方案和全部生图提示词做付费调度 A/B，因此不声称整套提速百分比或风格等效。94 项快速专项包含事件同步调度测试，证明无三秒错峰、慢图不阻塞补位；它们不替代视觉质量验证。人工抽看了真实主图，结果保持为 NOT_REVIEWED，没有新增交付阻塞审美审计。

## 跨境只读核查与后续建议

跨境继续 `xinghe-crossborder-detail-3`：按平台决定横版 2048×1152 或俄语平台 900×1200，独立页生成，人工披露/选择门槛，没有自动审美重绘授权。当前执行器的三秒错峰只属于精致详情；跨境仍有整批等待。后续可独立复制 Skill/profile 后审计共享锁重复与滚动调度，但应保留各平台画幅及既有人工交付边界。本次不新增跨境快速、不调整跨境设置或审核行为。
