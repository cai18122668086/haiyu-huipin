# 4.0.1 plan input robustness report

## Root cause and repair

The `D:\测试4\run-a989013a8bb8` task used the installed 4.0.0 Skill, but stopped before image generation. `plan-target-page` crashed on a plain-string `source_semantic_inventory` and the submitted plan used a flattened `semantic_translation` shape. The planner now accepts the two documented source-semantic forms, rejects flattened entries with a precise correction, and reports missing source IDs. Replaying the failed command now returns a controlled `WORKFLOW_ERROR` and preserves `PRODUCT_FACTS_READY`; no generation request is minted from an invalid plan.

The same thread also recorded external “Selected model is at capacity” failures. Those are service-capacity interruptions and remain separate from this deterministic plan-input defect.

Validation: `py -3.12 -m pytest -q` → 126 passed; `quick_validate.py` → `Skill is valid!`; no real imagegen or visual review.

## 4.0.0 method architecture convergence report

## Architecture convergence

The architecture review found a dead planning state, a misleading recovery action after plan failure, and an unresolved precedence conflict between “inherit reference typography” and the handwritten-form ban. 4.0.0 removes the dead state, makes persisted generation blocks visible in `next_action`, and states that safety/form bans take precedence over style inheritance. Existing Pair binding and product-identity guards remain in place.

Validation: 125 tests passed; quick_validate passed; no real imagegen or new visual review.

## Generation gate and crop binding hardening

The supplied test thread produced a visually invalid G01 despite carrying the typography and decorative-label bans. Its run had a persisted plan-audit block but still reached an image handoff, and its three generic mechanism crops were all assigned to T01 rather than covering the Pair's current source steps. The request-level and workflow-level gates now reject that state, require explicit `source_product_presence=false` and source-step bindings for non-fixture crops, and block any Pair request missing a mapped source step. Benchmark people, scenes, products, brands and identifiable objects are explicitly reference-only in the native handoff.

Validation: 124 tests passed; quick_validate passed; no real imagegen or new visual review.
# 3.9.6 method architecture repair report

## 3.9.6 Typography form machine gate

The supplied image shows a handwritten brush-script treatment. 3.9.6 bans that typography form in generation and visual audits while preserving all benchmark-matched text, specifications, evidence, usage instructions and information density. Audits with `forbidden_typography_form=true` cannot pass; regression coverage exercises the failure path.

Validation: `py -3.12 -m pytest -q` → 122 passed; `quick_validate.py` → `Skill is valid!`; no real imagegen or new visual review.

# 3.9.5 method architecture repair report

## 3.9.5 Unconfirmed product mark machine gate

A known test render introduced an unconfirmed English wordmark on the product body. 3.9.5 promotes the existing prompt and audit guidance into a machine contract: requests declare `ban_unconfirmed_product_marks=true`, and audits with `unconfirmed_product_mark=true` cannot pass. Regression coverage exercises this failure path. No test-thread product facts were added.

Validation: `py -3.12 -m pytest -q` → 121 passed; `quick_validate.py` → `Skill is valid!`; no real imagegen or new visual review.

# 3.9.4 方法架构修复交付报告

## 3.9.4 Pair 几何、密度上限与产品字样污染修复

针对户外骑行背包测试中出现的左右双栏、单屏过密和包体未确认产品名，生成提示新增完整 Pair 几何合同：上屏和下屏共享整幅宽度，以横向接缝连接，禁止左右分栏、长栏重排和额外填充内容。每屏的文字块、可见字数、细节图、参数行和主视觉预算现在是硬上限。产品本体不得出现未经确认的产品名、品牌名、Logo、水印或 wordmark，审查协议对此类污染直接失败。

验证：待本次修订完成后运行 `py -3.12 -m pytest -q` 与 `quick_validate.py`；未运行真实 imagegen。

## 3.9.3 单屏对标顺序与方法压平修复

本次修复了单屏对标详情页在 10 屏/5 Pair 输出中的信息丢失。每个 Pair 现在只接收当前目标绑定的参考裁剪图，并携带按物理顺序排列的源步骤、源图、内容职责、表达动作与交接。目标屏合并多个源步骤时，提示明确要求按顺序展开，不能只使用第一步。

新增非 fixture 方法压平闸门：长序列中若不同内容步骤共用同一套说服任务、证据义务和表达动作，工作流在生成前失败并要求补齐逐屏分析；实际重复的对标处理仍允许重复。源方法字段出现 Unicode replacement character 也会在生成前失败，避免编码损坏的文字进入提示。

验证：`py -3.12 -m pytest -q` → 120 passed；`quick_validate.py` → `Skill is valid!`。未运行真实 imagegen 或新的视觉审查。

## 3.9.2 标签载体提示词纠偏

修复生成提示词中“继承胶囊标题、编号徽标”与全局 AI 标签载体禁令的冲突。现在对标仍决定信息层级、节奏、密度和阅读路径；被禁的胶囊、徽章、贴纸、标签云和重复圆角卡片只转换为开放式正文、标题、注释、引线、规则或分栏，不会被正向提示复制。新增回归断言锁定该行为。

验证：工作副本 `py -3.12 -m pytest -q` 通过；`quick_validate.py` 通过。未运行真实 imagegen 或新的视觉审查。

## 3.9.1 对标驱动修订（最终交付）

纠正前一轮把测试中“专利没有被用到”泛化成 Skill 强制覆盖的问题。详情页事实选择和屏间展开现在完全服从已观察的对标步骤：专利、认证、检测等资料是可选来源，不会自动新增屏幕、改变卖点顺序、要求恰好出现一次或触发跨组去重；对标重复即可重复，对标区分才区分。证据门只对实际选入画面的高风险文案生效。

## 3.9.1 合规分级而非全量证据门

增加四档文案风险合同，避免把所有卖点都变成“必须提供检测报告”。普通产品事实可直接使用；高风险客观宣称绑定证据；品类敏感词进行类别复核；明确违法或欺骗性表达阻断。生成请求、详情计划和原生 prompt 保持同一合同。

## 3.9.0 卖点规划与证据覆盖

修复详情页“专利未出现、同一卖点多组重复、末屏内部收束词进入画面”的通用规划漏洞。多产品输入现在必须先做证据清单；优先证据事实必须覆盖且只覆盖一屏；源步骤展开到多屏时必须有不同的对标表达依据；收束/总结/结尾只作内部职责，不进入可见文案。

验证：117 tests passed（`py -3.12 -m pytest -q`）；quick_validate 通过；本次未重新运行真实 imagegen。

## 3.8.9 标签形式纠偏

修正 3.8.8 对用户意图的过度解释：禁止的是 AI 标签载体，不是文字、说明或信息密度。生成请求继续继承对标的文字块、可见字数、证据层级和阅读顺序；删除了 Skill 自定义的统一高级感合同，改为要求高级感和每屏展开从已观察的对标机制推导。

## 3.8.8 启动门修复

本次升级针对真实测试线程中“读取 Skill 后直接调用 imageGeneration”的绕过路径：适用请求必须先创建或恢复持久化 run；在计划审计通过、活动生成请求存在且状态为 `GENERATION_ALLOWED` 之前，不得调用任何图像工具。原生工具只能通过 `imagegen_adapter.py` 的 handoff 调用。新增 Skill、README、执行参考、界面默认提示和回归测试约束；没有改变详情页 10 屏、5 组 Pair、3:8、源驱动转译、一次性图文生成和事实安全合同。

本次修复只落实通用方法：先拆对标的买家问题、说服任务、证据义务和表达动作，再进行产品事实转译；没有把任何测试产品内容写入 Skill。

## 已完成

- 查明该详情任务的 G01 原生尺寸为 768×2048，接受文件为其 LANCZOS 放大的 1440×3840 版本；逐像素比对 RMS 为 0。
- 接入原始文件与交付副本的本地核验脚本、详情记录必需的回执、非 fixture 审查的原件复核。
- 保留两屏一组、当前屏紧凑 prompt、风格机制、产品叙事和图文同时生成要求。
- 拒绝 fixture 覆盖真实审查失败；最终清单按逐图实际证据区分 fixture 与外部视觉审查。
- 屏幕编号只保留在机器请求和工作流状态，不进入模型画面指令；G01 比例协商后写入后续 Pair 和交付切屏。其它路由继续使用原来的单视觉单元 prompt。
- 同步命令入口、恢复指引、generation/render-receipt/audit Schema、文档和回归测试。

## 未完成

- 尚未对新版本做真实 imagegen 生成与视觉质量验收；本次未重新生成旧任务的图片。
- 不能将本次修复表述为“已保证长图清晰或文字不会乱码”。它解决的是低清放大后被误收的漏洞。

## 外部依赖

- 内置 `$imagegen` 返回的真实原始图片。尺寸写在 prompt 中，工具没有暴露独立的 size/quality 参数。
- agent 根据真实工具结果绑定原始路径，然后用 `render_provenance.py` 生成本地回执；工具本身不签发该回执。
- 逐图人工或视觉模型审查，检查文字、纹理、风格、产品身份与事实。

## 已知限制

- 文件比对只能验证原件尺寸、两份文件是否相同及是否被篡改；不能鉴定服务商来源或自动证明视觉清晰。
- 高像素仍不保证文字正确。模型若仅返回低清原件，当前运行会被尺寸闸门阻止，不能通过放大、改状态、fixture 或静默换 API 绕过。
- 现有旧 run 的历史收图与审查不会被本次安装追溯改写。

## 测试命令与结果

- pytest: 101 passed in 31.08s (py -3.12 -X utf8 -m pytest -q -p no:cacheprovider tests).
- `py -3.12 -X utf8 C:/Users/我的电脑/.codex/skills/.system/skill-creator/scripts/quick_validate.py <skill-dir>`：Skill is valid。
- .skill 与 ZIP 包均 39 个文件；CRC、逐文件内容一致性、无 cache/pyc 校验通过。SHA-256 见外置 artifacts/change-audit-final.json。
- 覆盖：所有既有路由、五组详情与十屏顺序切片、逐屏密度/边界合同、原生低清拒绝、缺失回执、尺寸/哈希/原件篡改、原样副本成功、真实审查失败后的 fixture 拒绝、最终质量标签的证据边界。

本次真实 imagegen 0 次、人工视觉通过 0 次、视觉模型通过 0 次。测试里构造的 manual 审查数据也属于 deterministic/fixture 测试证据。

## 分发包与备份

- 当前安装目录：C:/Users/我的电脑/.codex/skills/all-category-style-benchmark-still（仍为 3.9.1；3.9.3 未安装）
- .skill：D:/askill/all-category-style-benchmark-still-v3.9.3.skill
- 外层 ZIP：D:/askill/all-category-style-benchmark-still-v3.9.3.zip
- 完整 ZIP：D:/askill/all-category-style-benchmark-still-v3.9.3-complete.zip
- 修改前 3.7.2 备份：D:/askill/repairs/detail-pair-3.8.0-20260908/before
- 变更记录与验证证据：D:/askill/repairs/detail-pair-3.8.0-20260908/artifacts




## 3.8.2 本次修改

已完成逐屏密度预算和相邻屏边界合同；真实审查新增 density_checks / transition_checks。本次未调用真实 imagegen，101 项确定性测试通过；fixture 证据不代表视觉质量通过。

## 3.8.3 本次修改

针对海尔洗衣机详情页实图复核，Pair 母图改为原生宽高比 3:8 合同，移除固定 1440×3840 尺寸闸门；提示词增加跨缝锚点与参考字形形态约束，并同步 imagegen handoff、Schema、文档和回归测试。真实 imagegen 与视觉复核仍需在新任务中执行。

## 3.8.4 repair report

Fixed interruption continuity at the workflow boundary: the in-flight token is durable before handoff, JSON writes are atomic, and recovery validates the original request, prompt, run ID, token and product/reference hashes. A dedicated `resume-imagegen-request` command prevents a resumed run from minting a new request. Added interruption, tamper and missing-input tests.

Validation: `py -3.12 -X utf8 -m pytest -q -p no:cacheprovider tests` → 106 passed; quick_validate → Skill is valid. Real imagegen and visual review were not executed for this code repair.

## 3.8.7 continuation

Rechecked the opening-function repair on the non-fixture workflow path. Added semantic regressions that reject rewriting a `result_promise` benchmark opening as `problem_awareness` and allow `problem_awareness` when the benchmark itself opens with pain. The tests use generic synthetic method fields and do not encode any test-thread product facts.

Validation: `py -3.12 -X utf8 -m pytest -q -p no:cacheprovider tests` → 109 passed; quick_validate → Skill is valid. No real imagegen or visual review was run.

## 3.8.7 continuation — detail expansion follows the benchmark

Removed the detail-route requirement for caller-authored screen-by-screen buyer questions, answers, duties, visual roles and evidence bindings. Detail generation now uses the bound physical reference steps, their observed method fields, density budgets and transitions as the screen expansion source. Added a regression showing that a detail plan with those target-side story fields omitted still derives its opening function and emits a prompt carrying the reference question and method. Other routes retain their target contracts.

Validation: `py -3.12 -X utf8 -m pytest -q -p no:cacheprovider tests` → 110 passed; quick_validate → Skill is valid. No real imagegen or visual review was run.

## 3.8.7 continuation — global decorative-label ban and premium direction

Added a machine-checked prompt contract, all-route handoff rule and shared audit dimension for removing AI-looking decorative labels. The ban covers standalone icon-plus-short-word benefit blocks, pill/badge rows, floating tags, label clouds, filler icon rows and unsupported certification badges. The prompt now directs the model to build a more premium result through observed scale, whitespace, typography hierarchy, material realism, camera/light control, restrained decoration and a clear focal point. Existing product/specification and compliance text remains available as integrated editorial copy.

Validation: `py -3.12 -X utf8 -m pytest -q -p no:cacheprovider tests` → 110 passed; quick_validate → Skill is valid. No real imagegen or visual review was run.

