# 亚马逊 21 张套图·快速：实现与本地验收

日期：2026-10-29（北京时间）。分支 `codex/amazon-suite-fast-20261029`，独立 worktree `huipin-amazon-suite-fast-20261029`。仅操作私有仓库 `cai18122668086/haiyu-huipin`，未部署、未改生产 Compose、未推送 main，也未写其他窗口的工作树。

实现提交 `a837468`；已通过 `98ab58f` 合并 `origin/main` 的 `3f01230`，包含 `ce56815` 原版规划截断、原图交付和尺寸展示修复。合并冲突仅在 `xinghe_agent.py` 两处：保留主线通用 length 拒绝逻辑，DeepSeek bounded profile 在主线集合上增加快速 profile。没有复制未发布工作区的内容。

## 用户入口与原版隔离

亚马逊入口继续使用 `app/workspace/amazon-visual-suite/page.tsx` 的共享 `CommerceSkillWorkspace`。新增原版／快速选项，默认原版；快速才发送 `configuration.amazon_speed=fast`。本地工作区草稿记住所选版本，历史任务只读恢复创建时版本。缺省、null 和显式 standard 的序列化都省略新字段，维持原版请求哈希和持久设置。原 Skill、原 QA、两轮复审／失败项重绘与历史确认路径保持。

快速的 Skill 为 `xinghe-amazon-visual-suite-fast`，规划 profile 为 `amazon_visual_suite_fast`，方案策略为 `amazon-visual-suite-fast-v1`。Skill 完整复制原目录，新 SKILL 与 fast-planning 参考定义快速控制器；只加载这两份运行协议，原参考与脚本留存但不执行旧 QA CLI。独立 manifest 验证文件库存及内容，原版 manifest 不变。

## 实际执行策略

- 一次有效模型请求完成完整 21 项方案：1 主图、6 副图、7 桌面 A+、7 移动 A+。共享分析、主商品身份、授权事实、风格与布局策略只输出一次。逐项仍完整输出买家问题、卖点、置信度、视觉证明、文案、构图、来源及风险；仅由服务器填入固定类型和画幅，绝不补齐缺失创意或截取不完整 JSON。
- 主身份必须绑定真实白底商品角色中的某一 source ID。主图禁止新增文案；桌面和移动逐项独立创作，不能复用相同构图描述。编辑与确认同样执行快速完整性规则，确认时再次检查真实来源归属。
- 快速规划单路，不跨渠道再次规划；输出上限 32768，DeepSeek 使用兼容 JSON object 传输但仍发送完整 schema。reasoning 包含在额度内；即使 JSON 能解析，finish_reason=length 仍失败。模型与 high 生图设置保持现有真实渠道配置。
- 单套最多 20 张滚动并发，完成一张立即补第 21 张；仍遵守更小的 worker 上限、数据库活跃租约和全站共享图像准入。取消原版分组屏障，成功项不等慢项才释放空位。
- 不调用整套 AI 复审，不自动审美重绘，也不因未审视觉质量阻断交付。报告为 `NOT_REVIEWED`，技术完成与视觉通过分开显示。技术失败仍 partial/failed，未完成 21 张不能完整 ZIP 交付。
- 冻结方案签名、Skill hash、源图 SHA、角色和完整生图提示词。首次提交前逐项持久回执；响应返回后先存上游请求 ID、用量与诊断，再保存原始字节。未知提交或下载结果不重发。原图保存后、最终素材交付失败可在正常 retry API 中零调用复用。
- 显式失败项重试复用已确认整套方案，成功 siblings 留用。FULL/SELECTED 也过滤已成功／REUSED 项，避免把已有原图再扣一次积分。确实不合格的已知原图先按当前规则复验，只有明确新重试且仍不合格才允许重新提交；同 job 不自发重试，unknown 始终不能重发。
- 第一组本地付费回执创建时还没有完整 prompt 字段；兼容只接受从冻结方案重构后、与原绑定 SHA 完全相符的旧提示词。绑定不符拒绝。后续回执提交前直接保存完整 prompt，不随代码更新漂移。

## 原图交付与尺寸边界

请求画幅保持主图 2000×2000、副图 1500×2000、桌面 1464×600、移动 1600×1200，quality=high。快速版交付供应商原始 PNG/JPEG/WebP 字节，按实际分辨率和格式展示；不本地裁切、补边、拼接、拉伸、放大或覆盖文案，不声称这些原图是精确目标尺寸 JPEG。

原图必须可解码、比例误差≤1%、最长边≤10000。桌面仍至少 1464×600；其他画幅沿用已验证原图的高清阈值：短边≥1024，或短边≥768且至少150万像素。provider 与最终数据库交付边界重复检查同一规则；不符合则保留原图并阻止该项交付。目标像素和模型请求没有降低。这个阈值是本产品原图交付策略，不是所有亚马逊类目／模块均可直接上架的保证；本轮未登录 Seller Central 上传验收。

## 网站真实链路实测

使用已有浅蓝手柄杯单张素材，只提供可见颜色、手柄、透明杯盖、蓝色闭合件和金属色杯沿，禁止虚构容量、保温时长、防漏、材质等级、认证或赠品。通过真实网站上传、幂等创建、规划、worker、素材保存、签名原图和 ZIP API；SQLite 与存储均在本分支忽略目录 `backend/.data/amazon-suite-fast-live`。原始素材只读，未访问或改写生产数据。

沿用本机配置：provider `tengli`，planner `deepseek-v4.1-flash-1017`，image `image-2.5-flare`，所有 21 次生图 high。没有降模型、换渠道或调低画质。

1. 唯一付费组：1 次规划、21 次生图、0 次复审、0 次自动重绘。规划 105.947 秒；输入5791、输出15320、合计21111 tokens，其中 reasoning9907。前20张在 elapsed108.641–109.219秒提交；首原图141.688秒返回，141.907秒补第21张；最后原图296.469秒返回，运行结束297.703秒。
2. 该原进程仍使用精确像素相等校验，21张均被拦截，但全部供应商原图已持久保存。原 job failed，下载0、ZIP409，预留315积分最终结算0，余额恢复10000。这个失败证据保留，没有修改状态伪装成功。
3. 修正原图技术门槛后，先本地复验21张缓存，禁止任何 plan/edit/review 网络调用，再通过正常 failed retry API 恢复。恢复6.922秒，首签名可下载3.813秒；子任务 completed，21签名下载HTTP200、ZIP21，逐张 SHA 与供应商原图相同。新增规划／生图／复审全部0；预留315、结算315，余额9685、预留0。恢复 job 的 provider actual=0 只表示没有新增网络，首次真实调用实际费用仍 null，不能宣称免费。
4. 合并主线后的98ab58f再次 collect-only：21签名下载和 ZIP21 全部通过，SHA保持一致，新增模型调用0，余额仍9685。共42次成功签名读取对应21张不重复原图。

实际主图1254×1254；6张副图均1086×1448；桌面7张为1959–1962×802–803；移动7张均1448×1086。图片全部原样交付。两阶段耗时不能拼成一次连续新策略耗时；本轮没有重新跑第二组付费图片，也没有原版同输入盲评或冻结提示词的并发A/B，因此不宣称提速百分比或稳定SLA。

开发时抽样查看7张图片及全部规划文案：杯子身份与浅蓝色基本一致，主图白底无新增文字，桌面和移动构图独立。观察到 A-MOBILE-06 省略了规划文案，A-DESKTOP-06 增加了规划之外的描述性文字。没有将样本标成全套视觉PASS或自动重画。最终新调用提示词进一步直接绑定原始用户禁项，并明确要求逐项文案完整、准确呈现；这一加固已做合成回归，尚未再次付费验证实际成图改善。

## 验证与证据

最终合并基线两组后端回归分别120和127通过，文件集不重复，合计247项：快速规划23、快速执行／边界37，及原版亚马逊、自动启动、适配器、执行可靠性、精致详情、截断与原图兼容。最后提示词加固后再次跑37项快速执行测试全部通过。覆盖20共享 claim上限、慢图滚动补位、未知结果不重发、原图复用、乱序逐项下载、3种原图格式、低清／错比例、编辑非法来源、冻结锁篡改、legacy绑定和重复积分保护。

前端最终29项 Node测试、TypeScript、定向ESLint、生产构建通过。后端改动 Ruff、Mypy与diff检查通过；OpenAPI重新导出并同步生成类型。真实Edge本地浏览器确认默认原版、快速切换、刷新恢复及原图／未审说明；独立预览进程已关闭。测试只报既有FastAPI/httpx与jsonschema弃用警告。

安全摘要在 `verification/amazon-suite-fast-20261029/local-real-summary.json`，仅含计数、时间、用量、模型、画幅与SHA；无env、图像、完整提示词或完整私有回执。实际图片、ZIP、数据库、调用请求／回执、UI截图及完整日志仅在 `backend/.data` 忽略目录。复测脚本 `backend/scripts/verify_amazon_suite_fast.py` 默认仅检查准备条件；`--run` 才产生付费调用，已有数据库拒绝盲重跑；`--collect-only`零调用核验；`--recover-existing`复验缓存后只走正常retry且禁用模型调用。

## 共享文件冲突点与集成建议

新增专属模块为 `amazon_suite_fast.py`、`amazon_suite_fast_provider.py`、Skill／manifest、manifest脚本、验收脚本、专属前后端测试、`lib/amazon-suite-speed.ts`。

共享源修改清单：

- `backend/app/xinghe_agent.py`：仅快速 profile的协议、schema/prompt、单路／token/length、验证、mock与独立版本；完整沿用主线原版截断修复。
- `backend/app/execution.py`：注册独立快速 planner/provider；快速套图复用20滚动调度和共享claim上限，跳过原版分组/QA；最终NOT_REVIEWED与原图尺寸复核。
- `backend/app/amazon_visual_suite_provider.py`：快速编辑／确认规则与独立版本签名；原版 provider保持。
- `backend/app/amazon_visual_quality.py`：快速 job直接not_applicable，防意外走旧复审。
- `backend/app/schemas_v1.py`：可选amazon_speed与标准字段省略序列化；`skill_runtime.py`仅登记新Skill。
- `backend/app/routes_v1.py`：快速只预留初始21生图＋必要一次规划，不预留旧复审/重绘 envelope；保留原版预算路径。
- `backend/app/services.py`：仅快速重试过滤已成功项，正常保留siblings和积分结算。
- `components/commerce-skill-workspace.tsx`：仅亚马逊分支的版本／草稿／历史／NOT_REVIEWED展示，专属CSS；页面包装无需改。
- `docs/openapi-v1.json`、`lib/api/generated/openapi.d.ts`：自动生成的新字段。
- `tests/new-skill-workspaces.test.mjs`：两处断言随已合入main的精致详情实际尺寸文案更新，不改详情实现。

统一集成时优先在最新main合并本分支，按功能保留上述小块修改，不整包覆盖共享文件。没有数据库迁移、新环境变量或生产配置改动。生产切换仍交给统一集成窗口；本分支提交和实测不构成已上线。
